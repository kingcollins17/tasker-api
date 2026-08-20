
from typing import Any, Optional

from sqlalchemy import func, update

from celery import shared_task
from sqlmodel import select, col

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
    task_id: str,
    provider_id: str,
    payment_mode: str = "cash",
) -> Any:
    """Async handler for task completion.

    Updates task assignment and task status to COMPLETED, increments the provider's
    total completed task count and resets duty status to ONLINE_AVAILABLE via a single SQL update statement,
    and awards provider credibility.

    Args:
        task_id: Unique identifier for the completed task.
        provider_id: Unique identifier for the service provider completing the task.
        payment_mode: Mode of payment used for the task (defaults to "cash").

    Returns:
        Tuple of (service_id, category_id) if task exists, else (None, None).
    """
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        cred_service = get_credibility_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            task_repo = Repository(Task, session)
            assignment_repo = Repository(TaskAssignment, session)
            provider_profile_repo = Repository(ProviderProfile, session)

            # 1. Update task assignment status to COMPLETED and set completion timestamp
            stmt_assign = select(TaskAssignment).where(
                TaskAssignment.task_id == task_id
            )
            res_assign = await assignment_repo.execute(stmt_assign)
            assignment: Optional[TaskAssignment] = res_assign.one_or_none()
            if assignment:
                assignment.status = TaskAssignmentStatus.COMPLETED
                assignment.completed_at = lagos_now()
                await assignment_repo.add(assignment)

            # 2. Update parent task status to COMPLETED and retrieve service/category details
            service_id = None
            category_id = None
            task = await task_repo.get(task_id)
            if task:
                task.status = TaskStatus.COMPLETED
                await task_repo.add(task)
                service_id = task.service_id
                category_id = task.category_id

            # 3. Direct SQL update to increment provider's total_tasks_completed and set duty_status to ONLINE_AVAILABLE
            stmt_prof_update = (
                update(ProviderProfile)
                .where(col(ProviderProfile.user_id) == provider_id)
                .values(
                    total_tasks_completed=func.coalesce(col(ProviderProfile.total_tasks_completed), 0) + 1,
                    duty_status=DutyStatus.ONLINE_AVAILABLE,
                )
            )
            await provider_profile_repo.execute(stmt_prof_update)

            logger.info(
                f"complete_task_assignment: task {task_id} completed by provider {provider_id} (payment_mode={payment_mode})"
            )
            await system_logger.info(
                f"complete_task_assignment: task {task_id} completed by provider {provider_id} (payment_mode={payment_mode})",
                source="celery.complete_task_assignment",
            )

            # 4. Reward provider with credibility points for completing the task
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
    """Celery task to finalize task assignment completion.

    Executes DB operations via `_complete_task_assignment_async`, then dispatches
    asynchronous background tasks for processing payments and updating provider and service metrics.

    Args:
        task_id: Unique identifier of the task.
        provider_id: Unique identifier of the provider.
        payment_mode: Mode of payment ("cash", etc.).

    Returns:
        bool: True on successful queuing and execution.
    """
    logger.info(
        f"complete_task_assignment: task={task_id} provider={provider_id} payment_mode={payment_mode}"
    )
    # Execute database state updates (assignment, task status, provider profile update, credibility reward)
    task_info = run_async(
        _complete_task_assignment_async(task_id, provider_id, payment_mode)
    )
    if isinstance(task_info, tuple) and len(task_info) == 2:
        service_id, category_id = task_info
    else:
        service_id, category_id = None, None

    # Dispatch downstream asynchronous background tasks
    # 1. Process payment for the completed task
    # pyrefly: ignore [not-callable]
    process_task_payment.delay(task_id, provider_id, payment_mode)
    # 2. Sync provider statistics and metrics
    # pyrefly: ignore [not-callable]
    sync_provider_metrics.delay(provider_id)
    
    # 3. Sync service and category metrics if available
    if service_id and category_id:
        # pyrefly: ignore [not-callable]
        sync_service_metrics.delay(service_id=service_id, category_id=category_id)
    return True
