from datetime import datetime, timedelta
from typing import Any, Optional, Tuple, Union

from fastapi import Depends
from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.logging import logger
from app.core.models.credibility import CredibilityReason
from app.core.models.notifications import NotificationPriority, NotificationType
from app.core.models.tasks import (
    DispatchAttemptStatus,
    DispatchSession,
    DispatchSessionStatus,
    PaymentMode,
    Task,
    TaskAssignment,
    TaskAssignmentStatus,
    TaskDispatchAttempt,
    TaskStatus,
)
from app.core.models.users import DutyStatus, ProviderProfile
from app.core.repository import GetRepository, Repository
from app.core.services.logger_service import LoggerService, get_logger_service, get_logger_service_manual
from app.core.services.matching_engine import MatchingEngine
from app.core.utils.datetime_helper import lagos_now
from app.core.utils.timer import Timer
from app.features.credibility.services import (
    CredibilityService,
    get_credibility_service,
    get_credibility_service_manual,
)
from app.features.notifications.schemas import CreateNotification
from app.features.notifications.services import (
    NotificationService,
    get_notification_service,
    get_notification_service_manual,
)
from app.features.payments.celery.tasks import process_task_payment
from app.features.tasks.celery.metrics import (
    sync_provider_metrics,
    sync_service_metrics,
)


_LOG_SOURCE = "dispatch.service"


class DispatchEventService:
    """Core dispatch event service handling dispatch workflow initialization,
    provider ping responses, and task assignment completions directly in database transactions.
    """

    def __init__(
        self,
        session: AsyncSession,
        task_repo: Repository[Task],
        session_repo: Repository[DispatchSession],
        attempt_repo: Repository[TaskDispatchAttempt],
        assignment_repo: Repository[TaskAssignment],
        provider_profile_repo: Repository[ProviderProfile],
        system_logger: LoggerService,
        notification_service: NotificationService,
        credibility_service: CredibilityService,
    ):
        self.session = session
        self.task_repo = task_repo
        self.session_repo = session_repo
        self.attempt_repo = attempt_repo
        self.assignment_repo = assignment_repo
        self.provider_profile_repo = provider_profile_repo
        self.system_logger = system_logger
        self.notification_service = notification_service
        self.credibility_service = credibility_service

    
    async def handle_ping_response(
        self,
        task_id: str,
        provider_id: str,
        response_status: Union[DispatchAttemptStatus, str],
    ) -> None:
        """Processes ACCEPTED, DECLINED, or TIMEOUT for a dispatch ping."""
        # 1. Normalize status enum/string value
        status_val = (
            response_status.value
            if isinstance(response_status, DispatchAttemptStatus)
            else response_status
        )

        # 2. Fetch pending attempt
        stmt_attempt = select(TaskDispatchAttempt).where(
            TaskDispatchAttempt.task_id == task_id,
            TaskDispatchAttempt.provider_id == provider_id,
            TaskDispatchAttempt.status == DispatchAttemptStatus.PENDING,
        )
        res_attempt = await self.attempt_repo.execute(stmt_attempt)
        attempt: Optional[TaskDispatchAttempt] = res_attempt.one_or_none()
        if not attempt:
            msg = f"DispatchEventService.handle_ping_response: No PENDING attempt for task={task_id} provider={provider_id}"
            logger.warning(msg)
            await self.system_logger.warn(msg, source=_LOG_SOURCE, metadata={"task_id": task_id, "provider_id": provider_id})
            return

        # 3. Mark attempt status and responded timestamp
        now = lagos_now()
        new_status = (
            DispatchAttemptStatus.ACCEPTED
            if status_val == DispatchAttemptStatus.ACCEPTED.value
            else (
                DispatchAttemptStatus.DECLINED
                if status_val == DispatchAttemptStatus.DECLINED.value
                else DispatchAttemptStatus.TIMEOUT
            )
        )
        attempt.status = new_status
        attempt.responded_at = now
        await self.attempt_repo.add(attempt)

        await self.system_logger.info(
            f"DispatchEventService: Attempt {attempt.id} marked as {new_status.value} for task {task_id} provider {provider_id}",
            source=_LOG_SOURCE,
            metadata={"attempt_id": attempt.id, "task_id": task_id, "provider_id": provider_id, "status": new_status.value},
        )

        # 4. Delegate workflow execution based on outcome
        if new_status == DispatchAttemptStatus.ACCEPTED:
            await self._process_acceptance(task_id, provider_id, attempt.id, now)
        else:
            await self._process_decline_or_timeout(task_id, provider_id, new_status)

    async def _process_acceptance(
        self,
        task_id: str,
        provider_id: str,
        attempt_id: str,
        now: datetime,
    ) -> None:
        """Finalizes assignment when a provider accepts a ping attempt."""
        task = await self.task_repo.get(task_id)
        if task:
            task.status = TaskStatus.ASSIGNED
            await self.task_repo.add(task)

        # Bind provider in TaskAssignment
        stmt_assign = select(TaskAssignment).where(TaskAssignment.task_id == task_id)
        res_assign = await self.assignment_repo.execute(stmt_assign)
        assignment: Optional[TaskAssignment] = res_assign.one_or_none()
        if assignment:
            assignment.provider_id = provider_id
            assignment.status = TaskAssignmentStatus.ASSIGNED
            assignment.assigned_at = now
            await self.assignment_repo.add(assignment)
        else:
            new_assignment = TaskAssignment(
                task_id=task_id,
                provider_id=provider_id,
                status=TaskAssignmentStatus.ASSIGNED,
                assigned_at=now,
                created_at=now,
                updated_at=now,
            )
            await self.assignment_repo.add(new_assignment)

        # Set provider duty status to ON_TASK
        stmt_prof = select(ProviderProfile).where(ProviderProfile.user_id == provider_id)
        res_prof = await self.provider_profile_repo.execute(stmt_prof)
        profile: Optional[ProviderProfile] = res_prof.one_or_none()
        if profile:
            profile.duty_status = DutyStatus.ON_TASK
            await self.provider_profile_repo.add(profile)

        # Mark active dispatch session as ASSIGNED
        stmt_session = (
            update(DispatchSession)
            .where(
                DispatchSession.task_id == task_id,  # type: ignore
                DispatchSession.status == DispatchSessionStatus.SEARCHING,  # type: ignore
            )
            .values(
                status=DispatchSessionStatus.ASSIGNED,
                updated_at=now,
            )
        )
        await self.session_repo.execute(stmt_session)

        # Cancel any other concurrent pending attempts for this task
        stmt_cancel = (
            update(TaskDispatchAttempt)
            .where(
                TaskDispatchAttempt.task_id == task_id,  # type: ignore
                TaskDispatchAttempt.id != attempt_id,  # type: ignore
                TaskDispatchAttempt.status == DispatchAttemptStatus.PENDING,  # type: ignore
            )
            .values(
                status=DispatchAttemptStatus.CANCELED,
                responded_at=now,
            )
        )
        await self.attempt_repo.execute(stmt_cancel)

        # Notify customer via in_app and push channels
        if task and task.customer_id:
            await self.notification_service.notify(
                recepients=[task.customer_id],
                title="Provider Matched!",
                body=f"Great news! A provider has accepted your task '{task.title}'.",
                type=NotificationType.TASK_ACCEPTED,
                channels=["push", "in_app"],
                data={
                    "task_id": task.id,
                    "task_title": task.title,
                    "provider_id": provider_id,
                    "status": TaskStatus.ASSIGNED.value,
                    "type": "provider_matched",
                },
            )
            await self.system_logger.info(
                f"DispatchEventService: Sent provider_matched notification to customer {task.customer_id} for task {task_id}",
                source=_LOG_SOURCE,
                metadata={"task_id": task_id, "customer_id": task.customer_id, "provider_id": provider_id},
            )

        msg = f"DispatchEventService.handle_ping_response: task {task_id} ACCEPTED by provider {provider_id}"
        logger.info(msg)
        await self.system_logger.info(
            msg,
            source=_LOG_SOURCE,
            metadata={"task_id": task_id, "provider_id": provider_id, "attempt_id": attempt_id},
        )

    async def _process_decline_or_timeout(
        self,
        task_id: str,
        provider_id: str,
        new_status: DispatchAttemptStatus,
    ) -> None:
        """Handles decline or timeout by applying penalties and cascading the matching engine."""

        await self.credibility_service.add_credibility_entry(
            user_id=provider_id,
            reason=CredibilityReason.JOB_DECLINED,
            task_id=task_id,
        )
        await self.system_logger.info(
            f"DispatchEventService: Applied credibility penalty to provider {provider_id} for attempt timeout on task {task_id}",
            source=_LOG_SOURCE,
            metadata={"task_id": task_id, "provider_id": provider_id, "reason": "job_declined"},
        )

        msg = f"DispatchEventService.handle_ping_response: task {task_id} {new_status.value} by provider {provider_id}"
        logger.info(msg)
        await self.system_logger.info(
            msg,
            source=_LOG_SOURCE,
            metadata={"task_id": task_id, "provider_id": provider_id, "status": new_status.value},
        )



def get_dispatch_event_service(
    session: AsyncSession = Depends(get_session),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    session_repo: Repository[DispatchSession] = Depends(GetRepository(DispatchSession)),
    attempt_repo: Repository[TaskDispatchAttempt] = Depends(GetRepository(TaskDispatchAttempt)),
    assignment_repo: Repository[TaskAssignment] = Depends(GetRepository(TaskAssignment)),
    provider_profile_repo: Repository[ProviderProfile] = Depends(GetRepository(ProviderProfile)),
    system_logger: LoggerService = Depends(get_logger_service),
    notification_service: NotificationService = Depends(get_notification_service),
    credibility_service: CredibilityService = Depends(get_credibility_service),
) -> DispatchEventService:
    """FastAPI dependency returning an active ``DispatchEventService`` instance with injected dependencies."""
    return DispatchEventService(
        session=session,
        task_repo=task_repo,
        session_repo=session_repo,
        attempt_repo=attempt_repo,
        assignment_repo=assignment_repo,
        provider_profile_repo=provider_profile_repo,
        system_logger=system_logger,
        notification_service=notification_service,
        credibility_service=credibility_service,
    )

