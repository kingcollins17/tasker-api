from app.core.utils.timer import Timer
from app.core.services.logger_service import get_logger_service_manual

"""Celery dispatch tasks.

This module owns all dispatch business logic. ``DispatchEventService``
(in dispatch_service.py) is a stateless proxy that forwards calls here
via ``.delay()``.  Nothing outside this module should instantiate
``DispatchEventService`` for DB work.
"""

import random
from typing import Any, List, Optional
from pydantic import BaseModel

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
from app.core.models.users import (
    DutyStatus,
    KYCStatus,
    ProviderProfile,
    User,
    UserLocation,
)
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
from app.features.credibility.services import (
    CredibilityService,
    get_credibility_service_manual,
)
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


async def _start_dispatch_session_async(
    task_id: str,
    batch_size: int = 1,
    exclude_previous_sessions: bool = True,
    excluded_provider_ids: Optional[List[str]] = None,
    is_redispatch: bool = False,
    redispatch_reason: Optional[str] = None,
    search_radius_km: Optional[float] = 10.0,
    max_search_radius_km: Optional[float] = 30.0,
    auto_expand_radius: Optional[bool] = True,
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
                search_radius_km=search_radius_km,
                max_search_radius_km=max_search_radius_km,
                auto_expand_radius=auto_expand_radius,
                is_redispatch=is_redispatch,
                redispatch_reason=redispatch_reason,
                excluded_provider_ids=excluded_provider_ids,
                created_at=now,
                updated_at=now,
            )
            dispatch_session = await session_repo.add(dispatch_session)
        else:
            updated = False
            if excluded_provider_ids is not None:
                dispatch_session.excluded_provider_ids = excluded_provider_ids
                updated = True
            if is_redispatch:
                dispatch_session.is_redispatch = is_redispatch
                updated = True
            if redispatch_reason is not None:
                dispatch_session.redispatch_reason = redispatch_reason
                updated = True
            if search_radius_km is not None:
                dispatch_session.search_radius_km = search_radius_km
                updated = True
            if max_search_radius_km is not None:
                dispatch_session.max_search_radius_km = max_search_radius_km
                updated = True
            if auto_expand_radius is not None:
                dispatch_session.auto_expand_radius = auto_expand_radius
                updated = True
            if updated:
                dispatch_session.updated_at = lagos_now()
                await session_repo.add(dispatch_session)

        # pyrefly: ignore [not-callable]
        execute_matching_engine_task.delay(
            session_id=dispatch_session.id,
            exclude_previous_sessions=exclude_previous_sessions,
            excluded_provider_ids=excluded_provider_ids,
        )
        return dispatch_session.id


async def _execute_matching_engine_async(
    session_id: str,
    exclude_previous_sessions: bool = True,
    excluded_provider_ids: Optional[List[str]] = None,
) -> bool:
    """Instantiates an ephemeral MatchingEngine for session_id and executes one dispatch step."""
    async with async_session_maker() as session:
        engine = MatchingEngine(
            session_id=session_id,
            db_session=session,
            exclude_previous_sessions=exclude_previous_sessions,
            excluded_provider_ids=excluded_provider_ids,
        )
        return await engine.run()


# ---------------------------------------------------------------------------
# Public Celery tasks
# ---------------------------------------------------------------------------


@shared_task(name="tasks.start_dispatch_session_task")
def start_dispatch_session_task(
    task_id: str,
    batch_size: int = 5,
    exclude_previous_sessions: bool = True,
    excluded_provider_ids: Optional[List[str]] = None,
    is_redispatch: bool = False,
    redispatch_reason: Optional[str] = None,
    search_radius_km: Optional[float] = 10.0,
    max_search_radius_km: Optional[float] = 30.0,
    auto_expand_radius: Optional[bool] = True,
):
    """Celery task entrypoint to initialize a DispatchSession and trigger matching."""
    logger.info(f"start_dispatch_session_task: starting for task {task_id}")
    return run_async(
        _start_dispatch_session_async(
            task_id=task_id,
            batch_size=batch_size,
            exclude_previous_sessions=exclude_previous_sessions,
            excluded_provider_ids=excluded_provider_ids,
            is_redispatch=is_redispatch,
            redispatch_reason=redispatch_reason,
            search_radius_km=search_radius_km,
            max_search_radius_km=max_search_radius_km,
            auto_expand_radius=auto_expand_radius,
        )
    )


@shared_task(name="tasks.execute_matching_engine_task")
def execute_matching_engine_task(
    session_id: str,
    exclude_previous_sessions: bool = True,
    excluded_provider_ids: Optional[List[str]] = None,
):
    """Celery task entrypoint to run one step of ephemeral MatchingEngine."""
    logger.info(f"execute_matching_engine_task: running for session {session_id}")
    return run_async(
        _execute_matching_engine_async(
            session_id=session_id,
            exclude_previous_sessions=exclude_previous_sessions,
            excluded_provider_ids=excluded_provider_ids,
        )
    )
