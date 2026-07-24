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
from app.core.models.users import DutyStatus, KYCStatus, ProviderProfile, User
from app.core.repository import Repository
from app.core.services import RedisProviderLocationService, get_cache_service
from app.core.services.provider_location import NearbyProviderResult
from app.core.utils.celery import run_async
from app.core.utils.datetime_helper import utc_now
from app.features.notifications.schemas import CreateNotification
from sqlalchemy import func
from sqlmodel import col, select

from app.features.notifications.services import get_notification_service_manual
from app.features.tasks.celery.metrics import (
    sync_provider_metrics,
    sync_service_metrics,
)
from app.core.models.credibility import CredibilityLedgerEntry, CredibilityReason
from app.features.credibility.services import CredibilityService
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
    cache_service = get_cache_service()
    geo_service = RedisProviderLocationService(cache_service=cache_service)
    notification_service = get_notification_service_manual(session)
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
    )


# ---------------------------------------------------------------------------
# Internal async implementations
# ---------------------------------------------------------------------------
class _Candidate(BaseModel):
    provider_id: str
    distance_km: float
    score: float


async def _find_and_score_candidates(task_id: str, session) -> List[_Candidate]:
    """Discovers nearby providers, filters by eligibility, returns scored list."""
    (
        task_repo,
        task_location_repo,
        attempt_repo,
        assignment_repo,
        provider_profile_repo,
        user_repo,
        service_link_repo,
        geo_service,
        _,
    ) = await _make_dispatch_deps(session)

    task = await task_repo.get(task_id)
    if not task or not task.service_id:
        return []

    stmt_loc = select(TaskLocation).where(TaskLocation.task_id == task_id).limit(1)
    res_loc = await task_location_repo.execute(stmt_loc)
    task_loc: Optional[TaskLocation] = res_loc.scalar_one_or_none()
    if not task_loc or task_loc.latitude is None or task_loc.longitude is None:
        return []

    nearby_results: List[NearbyProviderResult] = (
        await geo_service.search_nearby_providers(
            latitude=task_loc.latitude,
            longitude=task_loc.longitude,
            radius_km=10.0,
            limit=100,
        )
    )
    if not nearby_results:
        return []

    nearby_map = {
        r.provider_id: r.distance_km
        for r in nearby_results
        if r.provider_id and r.distance_km is not None
    }
    provider_ids = list(nearby_map.keys())

    stmt_eligibility = (
        select(User, ProviderProfile)
        .join(ProviderProfile, ProviderProfile.user_id == User.id)  # type: ignore
        # type: ignore
        .join(
            ProviderServiceLink,
            # pyrefly: ignore [bad-argument-type]
            ProviderServiceLink.provider_id == ProviderProfile.user_id,
        )
        .where(
            User.id.in_(provider_ids),  # type: ignore
            User.is_active == True,  # noqa: E712
            ProviderProfile.is_online == True,  # noqa: E712
            ProviderProfile.duty_status == DutyStatus.ONLINE_AVAILABLE,
            ProviderProfile.status == KYCStatus.VERIFIED,
            ProviderServiceLink.service_id == task.service_id,
        )
    )
    res_eligibility = await provider_profile_repo.execute(stmt_eligibility)
    rows = res_eligibility.all()

    scored: list = []
    for user, profile in rows:
        dist_km = nearby_map.get(user.id, 10.0)
        acceptance_rate = (
            profile.acceptance_rate_30d
            if profile.acceptance_rate_30d is not None
            else 100.0
        )
        avg_rating = user.average_ratings if user.average_ratings is not None else 0.0
        credibility = (
            user.credibility_score if user.credibility_score is not None else 0.0
        )

        score = (
            (0.30 * acceptance_rate)
            + (0.25 * avg_rating * 20.0)
            + (0.25 * credibility)
            - (0.20 * dist_km)
        )
        scored.append(
            _Candidate(
                provider_id=user.id,
                distance_km=dist_km,
                score=round(score, 2),
            )
        )

    scored.sort(key=lambda c: c.score, reverse=True)
    return scored


async def _create_dispatch_attempt(
    task_id: str,
    provider_id: str,
    sequence_order: int,
    offered_payout: float,
    match_score: float,
    timeout_seconds: int,
    session,
) -> Optional[TaskDispatchAttempt]:
    """Persists a PENDING dispatch attempt and marks the provider ON_DISPATCH."""
    (
        task_repo,
        _,
        attempt_repo,
        _,
        provider_profile_repo,
        _,
        _,
        _,
        _,
    ) = await _make_dispatch_deps(session)

    now = utc_now()
    attempt = TaskDispatchAttempt(
        task_id=task_id,
        provider_id=provider_id,
        sequence_order=sequence_order,
        match_score=match_score,
        offered_payout=offered_payout,
        pinged_at=now,
        expires_at=now + timedelta(seconds=timeout_seconds),
        status=DispatchAttemptStatus.PENDING,
    )
    attempt = await attempt_repo.add(attempt)

    stmt_prof = select(ProviderProfile).where(ProviderProfile.user_id == provider_id)
    profile: Optional[ProviderProfile] = (
        await provider_profile_repo.execute(stmt_prof)
    ).scalar_one_or_none()
    if profile:
        profile.duty_status = DutyStatus.ON_DISPATCH
        await provider_profile_repo.add(profile)

    task = await task_repo.get(task_id)
    if task and task.status != TaskStatus.SEARCHING:
        task.status = TaskStatus.SEARCHING
        task.dispatch_started_at = task.dispatch_started_at or now
        task.current_attempt_sequence = sequence_order
        await task_repo.add(task)

    return attempt


async def _dispatch_next_candidate_async(task_id: str) -> None:
    """Picks the top unattempted candidate, issues a ping, and schedules the timeout task."""
    async with async_session_maker() as session:
        (
            task_repo,
            _,
            attempt_repo,
            _,
            _,
            _,
            _,
            _,
            notification_service,
        ) = await _make_dispatch_deps(session)

        task = await task_repo.get(task_id)
        if not task:
            logger.warning(f"dispatch_next_candidate: task {task_id} not found")
            return

        stmt_attempts = select(TaskDispatchAttempt).where(
            TaskDispatchAttempt.task_id == task_id
        )
        existing_attempts = list((await attempt_repo.execute(stmt_attempts)).all())

        attempted_ids = {a.provider_id for a in existing_attempts if a.provider_id}
        has_pending = any(
            a.status == DispatchAttemptStatus.PENDING for a in existing_attempts
        )
        if has_pending:
            logger.info(
                f"dispatch_next_candidate: task {task_id} already has a pending ping — skipping"
            )
            return

        candidates = await _find_and_score_candidates(task_id, session)
        unattempted = [c for c in candidates if c.provider_id not in attempted_ids]

        if not unattempted:
            task.status = TaskStatus.CANCELLED
            await task_repo.add(task)
            logger.info(
                f"dispatch_next_candidate: no candidates left for task {task_id} — status set to CANCELLED"
            )
            if task.customer_id:
                await notification_service.create_notification(
                    CreateNotification(
                        type=NotificationType.TASK_CANCELLED,
                        title="No Providers Available",
                        body=f"We couldn't find an available provider for your task '{task.title}'. The task has been cancelled.",
                        priority=NotificationPriority.HIGH,
                        recipient_ids=[task.customer_id],
                        channels=["push", "in_app"],
                        data={
                            "task_id": task.id,
                            "type": "task_cancelled",
                            "reason": "no_candidates_available",
                        },
                    )
                )
            return

        queue_size = len(candidates)
        timeout_seconds = _calculate_dynamic_ping_duration(queue_size)
        sequence_order = len(existing_attempts) + 1
        top = unattempted[0]
        offered_payout = task.provider_payout or 0.0

        attempt = await _create_dispatch_attempt(
            task_id=task_id,
            provider_id=top.provider_id,
            sequence_order=sequence_order,
            offered_payout=offered_payout,
            match_score=top.score,
            timeout_seconds=timeout_seconds,
            session=session,
        )
        if not attempt:
            return

        payout_fmt = (
            f"₦{offered_payout:,.2f}" if offered_payout > 0 else "offered price"
        )
        time_mins = round(timeout_seconds / 60, 1)

        await notification_service.create_notification(
            CreateNotification(
                type=NotificationType.TASK_ACCEPTED,
                title="New Task Offer",
                body=(
                    f"You have a new task offer '{task.title}' for "
                    f"{payout_fmt}. Tap to respond within {time_mins} mins!"
                ),
                priority=NotificationPriority.HIGH,
                recipient_ids=[top.provider_id],
                channels=["push", "in_app"],
                data={
                    "task_id": task.id,
                    "dispatch_attempt_id": attempt.id,
                    "offered_payout": offered_payout,
                    "expires_at": (
                        attempt.expires_at.isoformat() if attempt.expires_at else None
                    ),
                    "type": "job_ping",
                },
            )
        )

        # Schedule timeout — chained Celery task
        # pyrefly: ignore [not-callable]
        handle_dispatch_ping_timeout.apply_async(
            args=[task.id, top.provider_id],
            countdown=timeout_seconds,
        )
        logger.info(
            f"dispatch_next_candidate: pinged provider {top.provider_id} "
            f"for task {task_id} (timeout={timeout_seconds}s)"
        )


async def _handle_provider_response_async(
    task_id: str,
    provider_id: str,
    response_status: str,
) -> None:
    """Processes ACCEPTED, DECLINED, or TIMEOUT for a dispatch ping."""
    async with async_session_maker() as session:
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
        ) = await _make_dispatch_deps(session)

        stmt_attempt = select(TaskDispatchAttempt).where(
            TaskDispatchAttempt.task_id == task_id,
            TaskDispatchAttempt.provider_id == provider_id,
            TaskDispatchAttempt.status == DispatchAttemptStatus.PENDING,
        )
        attempt: Optional[TaskDispatchAttempt] = (
            await attempt_repo.execute(stmt_attempt)
        ).scalar_one_or_none()

        if not attempt:
            logger.warning(
                f"handle_provider_response: no pending attempt for "
                f"task={task_id} provider={provider_id}"
            )
            return

        now = utc_now()
        attempt.status = DispatchAttemptStatus(response_status)
        attempt.responded_at = now
        await attempt_repo.add(attempt)

        stmt_prof = select(ProviderProfile).where(
            ProviderProfile.user_id == provider_id
        )
        profile: Optional[ProviderProfile] = (
            await provider_profile_repo.execute(stmt_prof)
        ).scalar_one_or_none()

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

                    await notification_service.create_notification(
                        CreateNotification(
                            type=NotificationType.TASK_ACCEPTED,
                            title="Task Accepted!",
                            body=f"{provider_name} has accepted your task '{task.title}'.",
                            priority=NotificationPriority.HIGH,
                            recipient_ids=[task.customer_id],
                            channels=["push", "in_app"],
                            data={
                                "task_id": task.id,
                                "assignment_id": assignment.id,
                                "provider_id": provider_id,
                                "type": "task_accepted",
                            },
                        )
                    )

            # Cancel any other pending attempts
            stmt_cancel = select(TaskDispatchAttempt).where(
                TaskDispatchAttempt.task_id == task_id,
                TaskDispatchAttempt.id != attempt.id,
                TaskDispatchAttempt.status == DispatchAttemptStatus.PENDING,
            )
            pending_attempts = list((await attempt_repo.execute(stmt_cancel)).all())
            for pending in pending_attempts:
                pending.status = DispatchAttemptStatus.CANCELED
                await attempt_repo.add(pending)

            logger.info(
                f"handle_provider_response: task {task_id} ACCEPTED by provider {provider_id}"
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
            # Insert credibility penalty for declined/timed-out ping
            decline_reason = (
                CredibilityReason.JOB_TIMEOUT
                if DispatchAttemptStatus(response_status) == DispatchAttemptStatus.TIMEOUT
                else CredibilityReason.JOB_DECLINED
            )
            cred_service = CredibilityService(Repository(CredibilityLedgerEntry, session))
            await cred_service.add_credibility_entry(
                user_id=provider_id,
                reason=decline_reason,
                task_id=task_id,
            )
            # Cascade — enqueue rather than inline await
            # pyrefly: ignore [not-callable]
            dispatch_next_candidate.delay(task_id)


async def _complete_task_assignment_async(
    task_id: str, provider_id: str, payment_mode: str = "cash"
) -> Any:
    """Marks assignment + task COMPLETED, resets provider duty status, and sets selected payment_mode."""
    async with async_session_maker() as session:
        (
            task_repo,
            _,
            _,
            assignment_repo,
            provider_profile_repo,
            _,
            _,
            _,
            _,
        ) = await _make_dispatch_deps(session)

        stmt_assign = select(TaskAssignment).where(TaskAssignment.task_id == task_id)
        assignment: Optional[TaskAssignment] = (
            await assignment_repo.execute(stmt_assign)
        ).scalar_one_or_none()
        if assignment:
            assignment.status = TaskAssignmentStatus.COMPLETED
            assignment.completed_at = utc_now()
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
        profile: Optional[ProviderProfile] = (
            await provider_profile_repo.execute(stmt_prof)
        ).scalar_one_or_none()
        if profile:
            profile.total_tasks_completed = (profile.total_tasks_completed or 0) + 1
            profile.duty_status = DutyStatus.ONLINE_AVAILABLE
            await provider_profile_repo.add(profile)

        logger.info(
            f"complete_task_assignment: task {task_id} completed by provider {provider_id} (payment_mode={payment_mode})"
        )
        # Reward provider with credibility for completing a task
        cred_service = CredibilityService(Repository(CredibilityLedgerEntry, session))
        await cred_service.add_credibility_entry(
            user_id=provider_id,
            reason=CredibilityReason.TASK_COMPLETED,
            task_id=task_id,
        )
        return service_id, category_id


# ---------------------------------------------------------------------------
# Public Celery tasks
# ---------------------------------------------------------------------------


@shared_task(name="tasks.start_dispatch_workflow")
def start_dispatch_workflow(task_id: str):
    """Kicks off candidate discovery and the first dispatch ping for a task."""
    logger.info(f"start_dispatch_workflow: starting for task {task_id}")
    return run_async(_dispatch_next_candidate_async(task_id))


@shared_task(name="tasks.dispatch_next_candidate")
def dispatch_next_candidate(task_id: str):
    """Selects the next best candidate and issues a timed dispatch ping."""
    logger.info(f"dispatch_next_candidate: task {task_id}")
    return run_async(_dispatch_next_candidate_async(task_id))


@shared_task(name="tasks.handle_dispatch_ping_timeout")
def handle_dispatch_ping_timeout(task_id: str, provider_id: str):
    """Fires when a provider's ping window expires without a response."""
    logger.info(f"handle_dispatch_ping_timeout: task={task_id} provider={provider_id}")
    return run_async(
        _handle_provider_response_async(
            task_id, provider_id, DispatchAttemptStatus.TIMEOUT.value
        )
    )


@shared_task(name="tasks.process_provider_dispatch_response")
def process_provider_dispatch_response(
    task_id: str, provider_id: str, response_status: str
):
    """Processes a provider's explicit accept or decline of a dispatch ping."""
    logger.info(
        f"process_provider_dispatch_response: task={task_id} "
        f"provider={provider_id} status={response_status}"
    )
    return run_async(
        _handle_provider_response_async(task_id, provider_id, response_status)
    )


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
