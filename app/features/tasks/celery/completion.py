from typing import Any, Optional

from celery import shared_task
from sqlmodel import select

from app.core.database import async_session_maker
from app.core.logging import logger
from app.core.models.credibility import CredibilityReason
from app.core.models.tasks import (
    PaymentMode,
    Task,
    TaskAssignment,
    TaskAssignmentStatus,
    TaskStatus,
)
from app.core.models.users import DutyStatus, ProviderProfile
from app.core.repository import Repository
from app.core.services.logger_service import get_logger_service_manual
from app.core.utils.celery import run_async
from app.core.utils.datetime_helper import lagos_now
from app.core.utils.timer import Timer
from app.features.credibility.services import get_credibility_service_manual
from app.features.payments.celery.tasks import process_task_payment
from app.features.tasks.celery.metrics import (
    sync_provider_metrics,
    sync_service_metrics,
)


async def _complete_task_assignment_async(
    task_id: str, provider_id: str, payment_mode: str = "cash"
) -> Any:
    """Marks assignment + task COMPLETED, resets provider duty status, and sets selected payment_mode."""
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            task_repo = Repository(Task, session)
            assignment_repo = Repository(TaskAssignment, session)
            provider_profile_repo = Repository(ProviderProfile, session)

            stmt_assign = select(TaskAssignment).where(TaskAssignment.task_id == task_id)
            res_assign = await assignment_repo.execute(stmt_assign)
            assignment: Optional[TaskAssignment] = res_assign.one_or_none()
            if assignment:
                assignment.status = TaskAssignmentStatus.COMPLETED
                assignment.completed_at = lagos_now()
                await assignment_repo.add(assignment)

            service_id = None
            category_id = None
            task = await task_repo.get(task_id)
            if task:
                task.status = TaskStatus.COMPLETED
                selected_mode = (
                    PaymentMode(payment_mode)
                    if payment_mode in ("cash", "online")
                    else PaymentMode.CASH
                )
                task.payment_mode = selected_mode
                await task_repo.add(task)
                service_id = task.service_id
                category_id = task.category_id

            stmt_prof = select(ProviderProfile).where(
                ProviderProfile.user_id == provider_id
            )
            res_prof = await provider_profile_repo.execute(stmt_prof)
            profile: Optional[ProviderProfile] = res_prof.one_or_none()
            if profile:
                profile.total_tasks_completed = (profile.total_tasks_completed or 0) + 1
                profile.duty_status = DutyStatus.ONLINE_AVAILABLE
                await provider_profile_repo.add(profile)

            logger.info(
                f"complete_task_assignment: task {task_id} completed by provider {provider_id} (payment_mode={payment_mode})"
            )
            await system_logger.info(
                f"complete_task_assignment: task {task_id} completed by provider {provider_id} (payment_mode={payment_mode})",
                source="celery.complete_task_assignment",
            )
            # Reward provider with credibility for completing a task
            cred_service = get_credibility_service_manual(session)
            await cred_service.add(
                user_id=provider_id,
                reason=CredibilityReason.TASK_COMPLETED,
                task_id=task_id,
            )
            await system_logger.metric(
                "complete_task_assignment",
                timer.stop(),
                source="celery.complete_task_assignment",
            )
            return service_id, category_id

        except Exception as e:
            await system_logger.error(
                f"complete_task_assignment Failed: {str(e)}",
                source="celery.complete_task_assignment",
            )
            raise e


@shared_task(name="tasks.complete_task_assignment")
def complete_task_assignment(
    task_id: str, provider_id: str, payment_mode: str = "cash"
):
    """Finalises a task — marks COMPLETED, triggers process_task_payment task, resets duty status, and syncs metrics."""
    logger.info(
        f"complete_task_assignment: task={task_id} provider={provider_id} payment_mode={payment_mode}"
    )
    task_info = run_async(
        _complete_task_assignment_async(task_id, provider_id, payment_mode)
    )
    if isinstance(task_info, tuple) and len(task_info) == 2:
        service_id, category_id = task_info
    else:
        service_id, category_id = None, None

    # pyrefly: ignore [not-callable]
    process_task_payment.delay(task_id, provider_id, payment_mode)
    # pyrefly: ignore [not-callable]
    sync_provider_metrics.delay(provider_id)
    # pyrefly: ignore [not-callable]
    sync_service_metrics.delay(service_id=service_id, category_id=category_id)
    return True
