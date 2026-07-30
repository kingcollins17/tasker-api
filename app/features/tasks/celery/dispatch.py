from app.core.utils.timer import Timer
from app.core.services.logger_service import get_logger_service_manual
"""Celery dispatch tasks.

This module owns all dispatch business logic. ``DispatchEventService``
(in dispatch_service.py) is a stateless proxy that forwards calls here
via ``.delay()``.  Nothing outside this module should instantiate
``DispatchEventService`` for DB work.
"""

from typing import Any
from pydantic import BaseModel
from datetime import timedelta
from typing import List, Optional

from celery import shared_task

from app.core.database import async_session_maker
from app.core.logging import logger
from app.core.models.notifications import NotificationPriority, NotificationType
from app.core.models.services import ProviderServiceLink
from app.core.models.payments import DebtReason, ProviderDebt
from app.core.models.tasks import (
    DispatchAttemptStatus,
    DispatchSession,
    DispatchSessionStatus,
    PaymentMode,
    PaymentStatus,
    Task,
    TaskAssignment,
    TaskAssignmentStatus,
    TaskDispatchAttempt,
    TaskLocation,
    TaskStatus,
)
from app.core.models.transactions import Transaction, TransactionStatus, TransactionType
from app.core.services.payment import get_paystack_gateway
from app.core.models.users import DutyStatus, KYCStatus, ProviderProfile, User, UserLocation
from app.core.repository import Repository
from app.core.services import PostGISProviderLocationService, get_cache_service
from app.core.services.matching_engine import MatchingEngine
from app.core.services.provider_location import NearbyProviderResult
from app.core.utils.celery import run_async
from app.core.utils.datetime_helper import lagos_now
from app.features.notifications.schemas import CreateNotification
from sqlalchemy import func, update
from sqlmodel import col, select

from app.features.notifications.services import get_notification_service_manual
from app.core.services.availability_service import get_availability_service_manual
from app.features.tasks.celery.metrics import (
    sync_provider_metrics,
    sync_service_metrics,
)
from app.core.models.credibility import CredibilityLedgerEntry, CredibilityReason
from app.features.credibility.services import CredibilityService, get_credibility_service_manual
from app.features.payments.celery.tasks import process_task_payment

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _calculate_dynamic_ping_duration(candidate_count: int) -> int:
    """Returns ping window in seconds, clamped to [30, 300].

    Formula: 300 - (N - 1) * 30
    - N=1  → 300 s (5 min)
    - N=5  → 180 s (3 min)
    - N≥10 → 30 s
    """
    if candidate_count <= 1:
        return 300
    return max(30, min(300, 300 - (candidate_count - 1) * 30))


# ---------------------------------------------------------------------------
# Shared session-builder helper
# ---------------------------------------------------------------------------


async def _make_dispatch_deps(session):
    """Build the repository + service objects needed by all dispatch tasks."""
    geo_service = PostGISProviderLocationService(
        location_repo=Repository(UserLocation, session),
        provider_profile_repo=Repository(ProviderProfile, session),
    )
    notification_service = get_notification_service_manual(session)
    availability_service = get_availability_service_manual(session)
    return (
        Repository(Task, session),
        Repository(TaskLocation, session),
        Repository(TaskDispatchAttempt, session),
        Repository(TaskAssignment, session),
        Repository(ProviderProfile, session),
        Repository(User, session),
        Repository(ProviderServiceLink, session),
        geo_service,
        notification_service,
        availability_service,
    )



async def _handle_provider_response_async(
    task_id: str,
    provider_id: str,
    response_status: str,
) -> None:
    """Processes ACCEPTED, DECLINED, or TIMEOUT for a dispatch ping."""
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            (
                task_repo,
                _,
                attempt_repo,
                assignment_repo,
                provider_profile_repo,
                _,
                _,
                _,
                notification_service,
                _,
            ) = await _make_dispatch_deps(session)

            stmt_attempt = select(TaskDispatchAttempt).where(
                TaskDispatchAttempt.task_id == task_id,
                TaskDispatchAttempt.provider_id == provider_id,
                TaskDispatchAttempt.status == DispatchAttemptStatus.PENDING,
            )
            attempt: Optional[TaskDispatchAttempt] = (
                await attempt_repo.execute(stmt_attempt)
            ).one_or_none()

            if not attempt:
                logger.warning(
                    f"handle_provider_response: no pending attempt for "
                    f"task={task_id} provider={provider_id}"
                )
                await system_logger.warn(
                    f"handle_provider_response: no pending attempt for "
                    f"task={task_id} provider={provider_id}",
                    source='celery.handle_provider_response'
                )
                return

            now = lagos_now()
            attempt.status = DispatchAttemptStatus(response_status)
            attempt.responded_at = now
            await attempt_repo.add(attempt)

            stmt_prof = select(ProviderProfile).where(
                ProviderProfile.user_id == provider_id
            )
            profile: Optional[ProviderProfile] = (
                await provider_profile_repo.execute(stmt_prof)
            ).one_or_none()

            if DispatchAttemptStatus(response_status) == DispatchAttemptStatus.ACCEPTED:
                if profile:
                    profile.duty_status = DutyStatus.ON_TASK
                    profile.consecutive_declines = 0
                    await provider_profile_repo.add(profile)

                assignment = TaskAssignment(
                    task_id=task_id,
                    provider_id=provider_id,
                    accepted_dispatch_attempt_id=attempt.id,
                    accepted_price=attempt.offered_payout or 0.0,
                    assigned_at=now,
                    status=TaskAssignmentStatus.ASSIGNED,
                )
                await assignment_repo.add(assignment)

                task = await task_repo.get(task_id)
                if task:
                    task.status = TaskStatus.ASSIGNED
                    task.assigned_provider_id = provider_id
                    await task_repo.add(task)

                    if task.customer_id:
                        provider_name = "A provider"
                        if profile and profile.first_name:
                            provider_name = (
                                f"{profile.first_name} {profile.last_name or ''}".strip()
                            )

                        await notification_service.notify(
                            recepients=[task.customer_id],
                            title="Task Accepted!",
                            body=f"{provider_name} has accepted your task '{task.title}'.",
                            type=NotificationType.TASK_ACCEPTED,
                            channels=["push", "in_app"],
                            data={
                                "task_id": task.id,
                                "assignment_id": assignment.id,
                                "provider_id": provider_id,
                                "type": "task_accepted",
                            },
                        )

                # Mark active dispatch session as ASSIGNED via bulk UPDATE
                session_repo = Repository(DispatchSession, session)
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
                await session_repo.execute(stmt_session)

                # Cancel any other pending attempts via bulk UPDATE
                stmt_cancel = (
                    update(TaskDispatchAttempt)
                    .where(
                        TaskDispatchAttempt.task_id == task_id,  # type: ignore
                        TaskDispatchAttempt.id != attempt.id,  # type: ignore
                        TaskDispatchAttempt.status == DispatchAttemptStatus.PENDING,  # type: ignore
                    )
                    .values(
                        status=DispatchAttemptStatus.CANCELED,
                        responded_at=now,
                    )
                )
                await attempt_repo.execute(stmt_cancel)

                logger.info(
                    f"handle_provider_response: task {task_id} ACCEPTED by provider {provider_id}"
                )
                await system_logger.info(
                    f"handle_provider_response: task {task_id} ACCEPTED by provider {provider_id}",
                    source='celery.handle_provider_response'
                )

            else:
                # DECLINED or TIMEOUT
                if profile:
                    new_declines = (profile.consecutive_declines or 0) + 1
                    profile.consecutive_declines = new_declines
                    if new_declines >= 3:
                        profile.is_online = False
                        profile.duty_status = DutyStatus.OFFLINE
                    else:
                        profile.duty_status = DutyStatus.ONLINE_AVAILABLE
                    await provider_profile_repo.add(profile)

                logger.info(
                    f"handle_provider_response: task {task_id} {response_status} by "
                    f"provider {provider_id} — cascading to next candidate"
                )
                await system_logger.info(
                    f"handle_provider_response: task {task_id} {response_status} by "
                    f"provider {provider_id} — cascading to next candidate",
                    source='celery.handle_provider_response'
                )
                # Insert credibility penalty for declined/timed-out ping
                decline_reason = (
                    CredibilityReason.JOB_TIMEOUT
                    if DispatchAttemptStatus(response_status) == DispatchAttemptStatus.TIMEOUT
                    else CredibilityReason.JOB_DECLINED
                )
                cred_service = get_credibility_service_manual(session)
                await cred_service.add_credibility_entry(
                    user_id=provider_id,
                    reason=decline_reason,
                    task_id=task_id,
                )
                # Advance dispatch session via MatchingEngine
                session_repo = Repository(DispatchSession, session)
                stmt_active = select(DispatchSession).where(
                    DispatchSession.task_id == task_id,
                    DispatchSession.status == DispatchSessionStatus.SEARCHING,
                )
                res_active = await session_repo.execute(stmt_active)
                active_session: Optional[DispatchSession] = res_active.one_or_none()
                if active_session:
                    engine = MatchingEngine(session_id=active_session.id, db_session=session)
                    await engine.run()


            await system_logger.metric('handle_provider_response', timer.stop(), source='celery.handle_provider_response')
        except Exception as e:
            await system_logger.error(f'handle_provider_response Failed: {str(e)}', source='celery.handle_provider_response')
            raise e


            
async def _start_dispatch_session_async(
    task_id: str, batch_size: int = 1
) -> Optional[str]:
    """Creates a stateful DispatchSession in DB for a task and triggers the MatchingEngine Celery task."""
    async with async_session_maker() as session:
        task_repo = Repository(Task, session)
        session_repo = Repository(DispatchSession, session)

        task = await task_repo.get(task_id)
        if not task:
            logger.warning(f"_start_dispatch_session_async: Task {task_id} not found")
            return None

        if task.status != TaskStatus.SEARCHING:
            task.status = TaskStatus.SEARCHING
            task.dispatch_started_at = task.dispatch_started_at or lagos_now()
            await task_repo.add(task)

        stmt_existing = select(DispatchSession).where(
            DispatchSession.task_id == task_id,
            DispatchSession.status == DispatchSessionStatus.SEARCHING,
        )
        res_existing = await session_repo.execute(stmt_existing)
        # Do not call scalar, result is already a scalar from repo.execute method
        dispatch_session: Optional[DispatchSession] = res_existing.one_or_none()

        if not dispatch_session:
            now = lagos_now()
            dispatch_session = DispatchSession(
                task_id=task_id,
                status=DispatchSessionStatus.SEARCHING,
                batch_size=batch_size,
                current_batch=1,
                created_at=now,
                updated_at=now,
            )
            dispatch_session = await session_repo.add(dispatch_session)

        # pyrefly: ignore [not-callable]
        execute_matching_engine_task.delay(dispatch_session.id)
        return dispatch_session.id


async def _execute_matching_engine_async(session_id: str) -> bool:
    """Instantiates an ephemeral MatchingEngine for session_id and executes one dispatch step."""
    async with async_session_maker() as session:
        engine = MatchingEngine(session_id=session_id, db_session=session)
        return await engine.run()


# ---------------------------------------------------------------------------
# Public Celery tasks
# ---------------------------------------------------------------------------


@shared_task(name="tasks.start_dispatch_session_task")
def start_dispatch_session_task(task_id: str, batch_size: int = 1):
    """Celery task entrypoint to initialize a DispatchSession and trigger matching."""
    logger.info(f"start_dispatch_session_task: starting for task {task_id}")
    return run_async(_start_dispatch_session_async(task_id, batch_size))


@shared_task(name="tasks.execute_matching_engine_task")
def execute_matching_engine_task(session_id: str):
    """Celery task entrypoint to run one step of ephemeral MatchingEngine."""
    logger.info(f"execute_matching_engine_task: running for session {session_id}")
    return run_async(_execute_matching_engine_async(session_id))








