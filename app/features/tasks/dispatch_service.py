import random
from datetime import datetime
from typing import Any, Optional, Tuple, Union

from fastapi import Depends, HTTPException, status
from sqlalchemy import func, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.logging import logger
from app.core.models.credibility import CredibilityReason
from app.core.models.notifications import NotificationType
from app.core.models.tasks import (
    CancelledBy,
    DispatchAttemptStatus,
    DispatchSession,
    DispatchSessionStatus,
    DispatchSessionTrigger,
    Task,
    TaskAssignment,
    TaskAssignmentStatus,
    TaskDispatchAttempt,
    TaskDispatchStatus,
    TaskStatus,
)
from app.core.models.users import DutyStatus, ProviderProfile
from app.core.repository import GetRepository, Repository
from app.core.services.dispatch_policy import DispatchPolicy
from app.core.services.logger_service import (
    LoggerService,
    get_logger_service,
    get_logger_service_manual,
)
from app.core.services.matching_engine import MatchingEngine
from app.core.utils.datetime_helper import lagos_now
from app.features.credibility.services import (
    CredibilityService,
    get_credibility_service,
)
from app.features.notifications.services import (
    NotificationService,
    get_notification_service,
)
from app.features.tasks.services import TaskService, get_task_service

_LOG_SOURCE = "dispatch.service"


class DispatchService:
    """Core dispatch service managing dispatch sessions, lifecycle transitions,
    concurrency locking, auto-retry policy scheduling, and provider ping responses.
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
        task_service: TaskService,
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
        self.task_service = task_service

    async def _get_next_sequence(self, task_id: str) -> int:
        """Computes next dispatch session sequence integer for a task."""
        stmt_max = select(func.max(DispatchSession.sequence)).where(
            DispatchSession.task_id == task_id
        )
        res = await self.session_repo.execute(stmt_max)
        raw_val = res.one_or_none()
        max_seq = (raw_val[0] if isinstance(raw_val, (tuple, list)) else raw_val) or 0
        return max_seq + 1

    async def start_initial_dispatch(self, task_id: str) -> Optional[DispatchSession]:
        """Initializes first dispatch session cycle for a task."""
        stmt_lock = select(Task).where(Task.id == task_id).with_for_update()
        res_task = await self.task_repo.execute(stmt_lock)
        task: Optional[Task] = res_task.one_or_none()

        if not task or task.status in (TaskStatus.ASSIGNED, TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            logger.info(f"DispatchService: Task {task_id} not eligible for initial dispatch.")
            return None

        now = lagos_now()
        task.dispatch_status = TaskDispatchStatus.DISPATCHING
        task.dispatch_claimed_at = now
        task.status = TaskStatus.SEARCHING
        task.dispatch_started_at = task.dispatch_started_at or now
        await self.task_repo.add(task)

        seq = await self._get_next_sequence(task_id)
        dispatch_session = DispatchSession(
            task_id=task_id,
            trigger=DispatchSessionTrigger.INITIAL,
            sequence=seq,
            status=DispatchSessionStatus.RUNNING,
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        dispatch_session = await self.session_repo.add(dispatch_session)
        await self.session.commit()

        engine = MatchingEngine(session_id=dispatch_session.id, db_session=self.session)
        result = await engine.run()

        if not result.matched:
            await self.handle_no_match(task_id=task_id, session_id=dispatch_session.id, reason=result.reason)

        return dispatch_session

    async def process_auto_retry(self, task_id: str) -> Optional[DispatchSession]:
        """Executes an automatic dispatch retry cycle for a due task."""
        stmt_lock = select(Task).where(Task.id == task_id).with_for_update()
        res_task = await self.task_repo.execute(stmt_lock)
        task: Optional[Task] = res_task.one_or_none()

        if not task or task.status in (TaskStatus.ASSIGNED, TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            logger.info(f"DispatchService: Task {task_id} not eligible for auto retry.")
            return None

        current_auto_count = task.auto_dispatch_count or 0
        if not DispatchPolicy.can_auto_retry(current_auto_count):
            logger.info(f"DispatchService: Task {task_id} auto dispatch limit reached.")
            await self._handle_auto_exhaustion(task)
            return None

        now = lagos_now()
        task.auto_dispatch_count = current_auto_count + 1
        task.dispatch_status = TaskDispatchStatus.DISPATCHING
        task.dispatch_claimed_at = now
        task.status = TaskStatus.SEARCHING
        await self.task_repo.add(task)

        seq = await self._get_next_sequence(task_id)
        dispatch_session = DispatchSession(
            task_id=task_id,
            trigger=DispatchSessionTrigger.AUTO_RETRY,
            sequence=seq,
            status=DispatchSessionStatus.RUNNING,
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        dispatch_session = await self.session_repo.add(dispatch_session)
        await self.session.commit()

        engine = MatchingEngine(session_id=dispatch_session.id, db_session=self.session)
        result = await engine.run()

        if not result.matched:
            await self.handle_no_match(task_id=task_id, session_id=dispatch_session.id, reason=result.reason)

        return dispatch_session

    async def manual_redispatch(
        self,
        task_id: str,
        current_user_id: str,
        feedback: Optional[str] = None,
    ) -> Task:
        """Triggers customer-initiated manual redispatch for a task."""
        stmt_lock = select(Task).where(Task.id == task_id).with_for_update()
        res_task = await self.task_repo.execute(stmt_lock)
        task: Optional[Task] = res_task.one_or_none()

        if not task:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

        if task.customer_id != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to redispatch this task",
            )

        current_manual_count = task.manual_dispatch_count or 0
        if not DispatchPolicy.can_manual_redispatch(current_manual_count):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum allowed customer redispatches ({DispatchPolicy.MANUAL_DISPATCH_MAX}) reached for this task.",
            )

        now = lagos_now()

        # If currently assigned, cancel assignment and release provider
        if task.status == TaskStatus.ASSIGNED and task.assignment:
            old_provider_id = task.assignment.provider_id
            task.assignment.status = TaskAssignmentStatus.CANCELLED
            await self.assignment_repo.add(task.assignment)

            if old_provider_id:
                stmt_duty = (
                    update(ProviderProfile)
                    .where(col(ProviderProfile.user_id) == old_provider_id)
                    .values(duty_status=DutyStatus.ONLINE_AVAILABLE)
                )
                await self.provider_profile_repo.execute(stmt_duty)

        task.assigned_provider_id = None
        task.manual_dispatch_count = current_manual_count + 1
        task.dispatch_status = TaskDispatchStatus.DISPATCHING
        task.dispatch_claimed_at = now
        task.status = TaskStatus.SEARCHING
        await self.task_repo.add(task)

        seq = await self._get_next_sequence(task_id)
        dispatch_session = DispatchSession(
            task_id=task_id,
            trigger=DispatchSessionTrigger.MANUAL,
            sequence=seq,
            status=DispatchSessionStatus.RUNNING,
            reason=feedback,
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        dispatch_session = await self.session_repo.add(dispatch_session)
        await self.session.commit()

        engine = MatchingEngine(session_id=dispatch_session.id, db_session=self.session)
        result = await engine.run()

        if not result.matched:
            await self.handle_no_match(task_id=task_id, session_id=dispatch_session.id, reason=result.reason)

        await self.task_repo.refresh(task)
        return task

    async def handle_no_match(
        self,
        task_id: str,
        session_id: str,
        reason: Optional[str] = None,
    ) -> None:
        """Handles session completion when candidate matching yields no available candidates."""
        task = await self.task_repo.get(task_id)
        dispatch_session = await self.session_repo.get(session_id)

        now = lagos_now()
        if dispatch_session:
            dispatch_session.status = DispatchSessionStatus.FAILED
            dispatch_session.completed_at = now
            dispatch_session.reason = reason or "No eligible candidate providers found"
            await self.session_repo.add(dispatch_session)

        if not task:
            return

        current_auto_count = task.auto_dispatch_count or 0
        if DispatchPolicy.can_auto_retry(current_auto_count):
            delay = DispatchPolicy.get_next_retry_delay(current_auto_count)
            task.dispatch_status = TaskDispatchStatus.RETRY_SCHEDULED
            task.next_dispatch_at = now + delay
            await self.task_repo.add(task)

            await self.task_service.log_task_event(
                task_id=task_id,
                event="AUTO_RETRY_SCHEDULED",
                reason=f"Scheduled auto retry attempt {current_auto_count + 1} in {delay.total_seconds()}s",
                task_status=task.status.value,
            )
        else:
            await self._handle_auto_exhaustion(task, dispatch_session)

    async def _handle_auto_exhaustion(
        self,
        task: Task,
        dispatch_session: Optional[DispatchSession] = None,
    ) -> None:
        """Marks task and session as auto-exhausted and notifies customer."""
        now = lagos_now()
        task.dispatch_status = TaskDispatchStatus.AUTO_EXHAUSTED
        task.status = TaskStatus.EXPIRED
        task.cancellation_reason = "No available provider accepted the task after maximum automatic retries."
        await self.task_repo.add(task)

        if dispatch_session:
            dispatch_session.status = DispatchSessionStatus.EXHAUSTED
            dispatch_session.completed_at = now
            await self.session_repo.add(dispatch_session)

        await self.task_service.log_task_event(
            task_id=task.id,
            event="AUTO_RETRY_EXHAUSTED",
            reason="Exhausted maximum automatic dispatch retries.",
            task_status=task.status.value,
        )

        if task.customer_id:
            await self.notification_service.notify(
                recepients=[task.customer_id],
                title="No Providers Available",
                body=f"We couldn't find an available provider for your task '{task.title}' after multiple attempts. The task has expired.",
                type=NotificationType.TASK_CANCELLED,
                channels=["PUSH", "IN_APP"],
                data={
                    "task_id": task.id,
                    "type": "TASK_EXPIRED",
                    "reason": "auto_retry_exhausted",
                },
            )

    async def handle_ping_response(
        self,
        task_id: str,
        provider_id: str,
        response_status: Union[DispatchAttemptStatus, str],
    ) -> None:
        """Processes ACCEPTED, DECLINED, or TIMEOUT for a provider dispatch ping."""
        status_val = (
            response_status.value
            if isinstance(response_status, DispatchAttemptStatus)
            else response_status
        )

        stmt_attempt = select(TaskDispatchAttempt).where(
            TaskDispatchAttempt.task_id == task_id,
            TaskDispatchAttempt.provider_id == provider_id,
            TaskDispatchAttempt.status == DispatchAttemptStatus.PENDING,
        )
        res_attempt = await self.attempt_repo.execute(stmt_attempt)
        attempt: Optional[TaskDispatchAttempt] = res_attempt.one_or_none()
        if not attempt:
            logger.warning(f"DispatchService.handle_ping_response: No PENDING attempt for task={task_id} provider={provider_id}")
            return

        task = await self.task_repo.get(task_id)
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

        if new_status == DispatchAttemptStatus.ACCEPTED:
            if task and task.status == TaskStatus.ASSIGNED:
                logger.warning(f"DispatchService: task {task_id} already assigned; ignoring late acceptance from {provider_id}")
                return

            stmt_existing_accept = select(TaskDispatchAttempt).where(
                TaskDispatchAttempt.task_id == task_id,
                TaskDispatchAttempt.status == DispatchAttemptStatus.ACCEPTED,
                TaskDispatchAttempt.provider_id != provider_id,
            )
            res_existing_accept = await self.attempt_repo.execute(stmt_existing_accept)
            if res_existing_accept.one_or_none():
                logger.warning(f"DispatchService: another provider already accepted task {task_id}")
                return

        attempt.status = new_status
        attempt.responded_at = now
        await self.attempt_repo.add(attempt)

        if new_status == DispatchAttemptStatus.ACCEPTED:
            await self._process_acceptance(task_id, provider_id, attempt.id, now)
        else:
            await self._process_decline_or_timeout(task_id, provider_id, attempt)

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
            task.dispatch_status = TaskDispatchStatus.MATCHED
            task.assigned_provider_id = provider_id
            await self.task_repo.add(task)

        stmt_assign = select(TaskAssignment).where(TaskAssignment.task_id == task_id)
        res_assign = await self.assignment_repo.execute(stmt_assign)
        assignment: Optional[TaskAssignment] = res_assign.one_or_none()
        if assignment:
            assignment.provider_id = provider_id
            assignment.status = TaskAssignmentStatus.ASSIGNED
            assignment.assigned_at = now
            if not assignment.identity_pin:
                assignment.identity_pin = f"{random.randint(0, 9999):04d}"
            await self.assignment_repo.add(assignment)
        else:
            new_assignment = TaskAssignment(
                task_id=task_id,
                provider_id=provider_id,
                status=TaskAssignmentStatus.ASSIGNED,
                assigned_at=now,
                identity_pin=f"{random.randint(0, 9999):04d}",
                created_at=now,
                updated_at=now,
            )
            await self.assignment_repo.add(new_assignment)

        await self.task_service.log_task_event(
            task_id=task_id,
            event="task_assigned",
            reason="Provider accepted dispatch and task was assigned",
            provider_id=provider_id,
            assigned_provider_id=provider_id,
            task_status=TaskStatus.ASSIGNED.value,
            assignment_id=(assignment.id if assignment else None),
        )

        stmt_prof = select(ProviderProfile).where(ProviderProfile.user_id == provider_id)
        res_prof = await self.provider_profile_repo.execute(stmt_prof)
        profile: Optional[ProviderProfile] = res_prof.one_or_none()
        if profile:
            profile.duty_status = DutyStatus.ON_TASK
            await self.provider_profile_repo.add(profile)

        stmt_session = (
            update(DispatchSession)
            .where(
                col(DispatchSession.task_id) == task_id,
                col(DispatchSession.status) == DispatchSessionStatus.RUNNING,
            )
            .values(
                status=DispatchSessionStatus.ASSIGNED,
                completed_at=now,
                updated_at=now,
            )
        )
        await self.session_repo.execute(stmt_session)

        stmt_cancel = (
            update(TaskDispatchAttempt)
            .where(
                col(TaskDispatchAttempt.task_id) == task_id,
                col(TaskDispatchAttempt.id) != attempt_id,
                col(TaskDispatchAttempt.status) == DispatchAttemptStatus.PENDING,
            )
            .values(
                status=DispatchAttemptStatus.CANCELLED,
                responded_at=now,
            )
        )
        await self.attempt_repo.execute(stmt_cancel)

        if task and task.customer_id:
            provider_name = (
                profile.first_name.strip()
                if profile and profile.first_name and profile.first_name.strip()
                else "A provider"
            )
            await self.notification_service.notify(
                recepients=[task.customer_id],
                title="Matched!",
                body=f"{provider_name} has accepted your task '{task.title}'.",
                type=NotificationType.TASK_ACCEPTED,
                channels=["PUSH", "IN_APP"],
                data={
                    "task_id": task.id,
                    "task_title": task.title,
                    "provider_id": provider_id,
                    "status": TaskStatus.ASSIGNED.value,
                    "type": NotificationType.TASK_ACCEPTED.value,
                },
            )

    async def _process_decline_or_timeout(
        self,
        task_id: str,
        provider_id: str,
        attempt: TaskDispatchAttempt,
    ) -> None:
        """Handles decline/timeout by applying penalty, releasing provider, and checking session completion."""
        await self.credibility_service.add(
            user_id=provider_id,
            reason=CredibilityReason.JOB_DECLINED,
            task_id=task_id,
        )

        stmt_duty = (
            update(ProviderProfile)
            .where(
                col(ProviderProfile.user_id) == provider_id,
                col(ProviderProfile.duty_status) == DutyStatus.ON_DISPATCH,
            )
            .values(duty_status=DutyStatus.ONLINE_AVAILABLE)
        )
        await self.provider_profile_repo.execute(stmt_duty)

        # Check if there are any remaining pending attempts for this task/session
        stmt_pending = select(func.count(col(TaskDispatchAttempt.id))).where(
            TaskDispatchAttempt.task_id == task_id,
            TaskDispatchAttempt.status == DispatchAttemptStatus.PENDING,
        )
        res_pending = await self.attempt_repo.execute(stmt_pending)
        pending_count = res_pending.first() or 0

        if pending_count == 0:
            task = await self.task_repo.get(task_id)
            if task and task.status == TaskStatus.SEARCHING:
                session_id = attempt.dispatch_session_id
                if session_id:
                    await self.handle_no_match(task_id=task_id, session_id=session_id, reason="All candidate pings declined or timed out")


# Backward compatibility alias
DispatchEventService = DispatchService


def get_dispatch_service(
    session: AsyncSession = Depends(get_session),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    session_repo: Repository[DispatchSession] = Depends(GetRepository(DispatchSession)),
    attempt_repo: Repository[TaskDispatchAttempt] = Depends(GetRepository(TaskDispatchAttempt)),
    assignment_repo: Repository[TaskAssignment] = Depends(GetRepository(TaskAssignment)),
    provider_profile_repo: Repository[ProviderProfile] = Depends(GetRepository(ProviderProfile)),
    system_logger: LoggerService = Depends(get_logger_service),
    notification_service: NotificationService = Depends(get_notification_service),
    credibility_service: CredibilityService = Depends(get_credibility_service),
    task_service: TaskService = Depends(get_task_service),
) -> DispatchService:
    """FastAPI dependency returning an active ``DispatchService`` instance with injected dependencies."""
    return DispatchService(
        session=session,
        task_repo=task_repo,
        session_repo=session_repo,
        attempt_repo=attempt_repo,
        assignment_repo=assignment_repo,
        provider_profile_repo=provider_profile_repo,
        system_logger=system_logger,
        notification_service=notification_service,
        credibility_service=credibility_service,
        task_service=task_service,
    )


def get_dispatch_event_service(
    service: DispatchService = Depends(get_dispatch_service),
) -> DispatchService:
    """Backward compatible alias for get_dispatch_service."""
    return service
