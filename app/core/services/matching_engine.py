from sqlalchemy.orm import selectinload
from app.core.config import IS_LOCAL
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import Row, func, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.logging import logger
from app.core.models.notifications import NotificationType
from app.core.models.services import ProviderServiceLink
from app.core.models.tasks import (
    DispatchAttemptStatus,
    DispatchSession,
    DispatchSessionStatus,
    Task,
    TaskDispatchAttempt,
    TaskLocation,
    TaskStatus,
)
from app.core.models.users import (
    DutyStatus,
    KYCStatus,
    ProviderProfile,
    User,
    UserLocation,
)
from app.core.repository import QueryOptions, Repository
from app.core.services.availability_service import (
    AvailabilityService,
    get_availability_service_manual,
)
from app.core.services.logger_service import (
    LoggerService,
    get_logger_service_manual,
)
from app.core.services.provider_location import (
    NearbyProviderResult,
    PostGISProviderLocationService,
)
from app.core.utils.datetime_helper import lagos_now
from app.features.notifications.services import (
    NotificationService,
    get_notification_service_manual,
)

_LOG_SOURCE = "core.MatchingEngine"


@dataclass
class _ScoredCandidate:
    user_id: str
    distance_km: float
    score: float


class MatchingEngine:
    """Ephemeral matching engine that advances a task dispatch session by one step.

    Design Principles:
    1. Ephemeral: Instantiated per execution step with session_id and AsyncSession.
    2. Stateful DB Concurrency: Validates status == SEARCHING and uses optimistic
       concurrency locking (lock_version field) to prevent duplicate dispatch execution.
    3. Spatial & Multi-factor Ranking: Discovers candidates via PostGIS/spatial index,
       applies DB eligibility filters, scores candidate quality, and pings in batches.
    """

    def __init__(
        self,
        session_id: str,
        db_session: AsyncSession,
        ping_duration: int = 180,
        exclude_previous_sessions: bool = True,
        excluded_provider_ids: Optional[List[str]] = None,
        session_repo: Optional[Repository[DispatchSession]] = None,
        task_repo: Optional[Repository[Task]] = None,
        task_location_repo: Optional[Repository[TaskLocation]] = None,
        attempt_repo: Optional[Repository[TaskDispatchAttempt]] = None,
        provider_profile_repo: Optional[Repository[ProviderProfile]] = None,
        user_repo: Optional[Repository[User]] = None,
        geo_service: Optional[PostGISProviderLocationService] = None,
        notification_service: Optional[NotificationService] = None,
        availability_service: Optional[AvailabilityService] = None,
        system_logger: Optional[LoggerService] = None,
    ):

        self.session_id = session_id
        self.db_session = db_session
        self.ping_duration = ping_duration
        self.exclude_previous_sessions = exclude_previous_sessions
        self.excluded_provider_ids = (
            list(excluded_provider_ids) if excluded_provider_ids else []
        )

        self.session_repo = session_repo or Repository(DispatchSession, db_session)
        self.task_repo = task_repo or Repository(Task, db_session)
        self.task_location_repo = task_location_repo or Repository(
            TaskLocation, db_session
        )
        self.attempt_repo = attempt_repo or Repository(TaskDispatchAttempt, db_session)
        self.provider_profile_repo = provider_profile_repo or Repository(
            ProviderProfile, db_session
        )
        self.user_repo = user_repo or Repository(User, db_session)

        if geo_service is None:
            location_repo = Repository(UserLocation, db_session)
            geo_service = PostGISProviderLocationService(
                location_repo=location_repo,
                provider_profile_repo=self.provider_profile_repo,
            )
        self.geo_service = geo_service

        self.notification_service = (
            notification_service or get_notification_service_manual(db_session)
        )
        self.availability_service = (
            availability_service or get_availability_service_manual(db_session)
        )
        self.system_logger = system_logger or get_logger_service_manual(db_session)

    async def __get_eligible(
        self,
        provider_ids: List[str],
        service_id: str,
    ) -> List[Tuple[User, ProviderProfile]]:
        """Queries DB for active, verified, online providers matching service_id and not excluded."""
        if not provider_ids:
            return []

        stmt_eligibility = (
            select(User, ProviderProfile)
            .join(ProviderProfile, ProviderProfile.user_id == User.id)  # type: ignore
            .join(
                ProviderServiceLink,
                ProviderServiceLink.provider_id == ProviderProfile.user_id,  # type: ignore
            )
            .where(
                User.id.in_(provider_ids),  # type: ignore
                User.is_active == True,  # noqa: E712
                ProviderProfile.status == KYCStatus.VERIFIED,
                ProviderServiceLink.service_id == service_id,
                ProviderProfile.is_online == True,  # noqa: E712
                ProviderProfile.duty_status == DutyStatus.ONLINE_AVAILABLE,
            )
        )

        res_eligibility = await self.provider_profile_repo.execute(stmt_eligibility)
        return res_eligibility.unique().all()

    def __score_candidates(
        self,
        rows: List[Tuple[User, ProviderProfile]],
        nearby_map: Dict[str, float],
        batch_size: int,
        current_radius: float,
    ) -> List[_ScoredCandidate]:
        """Scores candidate providers by acceptance rate, rating, credibility, and distance, returning top candidates."""
        scored: List[_ScoredCandidate] = []
        for user, profile in rows:
            dist_km = nearby_map.get(user.id, 10.0)
            acceptance_rate = (
                profile.acceptance_rate_30d
                if profile.acceptance_rate_30d is not None
                else 100.0
            )
            avg_rating = (
                user.average_ratings if user.average_ratings is not None else 0.0
            )
            credibility = (
                user.credibility_score if user.credibility_score is not None else 0.0
            )

            normalized_dist = (dist_km / current_radius) * 100 if current_radius > 0 else 0
            score = (
                (0.30 * acceptance_rate)
                + (0.25 * avg_rating * 20.0)
                + (0.25 * credibility)
                - (0.20 * normalized_dist)
            )
            scored.append(
                _ScoredCandidate(
                    user_id=user.id,
                    distance_km=dist_km,
                    score=round(score, 2),
                )
            )

        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:batch_size]

    async def _update_search_progress(
        self,
        dispatch_session: Optional[DispatchSession],
        current_radius: float,
    ) -> None:
        """Persists updated search radius to the dispatch
        session, if changed. Deliberately does NOT touch `lock_version` — that field is owned
        exclusively by `run()`'s optimistic-lock step and must never be modified here."""
        if dispatch_session and (
            dispatch_session.search_radius_km != current_radius
        ):
            dispatch_session.search_radius_km = current_radius
            dispatch_session.updated_at = lagos_now()
            await self.session_repo.add(dispatch_session)

    async def _get_excluded_provider_ids(
        self,
        task_id: str,
        excluded_provider_ids: Optional[List[str]] = None,
        dispatch_session: Optional[DispatchSession] = None,
    ) -> List[str]:
        """Combines excluded provider IDs from constructor, method parameters, and DB dispatch attempts."""
        all_excluded: Set[str] = set(self.excluded_provider_ids)
        if excluded_provider_ids:
            all_excluded.update(excluded_provider_ids)

        stmt_attempts = select(TaskDispatchAttempt.provider_id).where(
            TaskDispatchAttempt.task_id == task_id
        )
        if not self.exclude_previous_sessions and dispatch_session:
            stmt_attempts = stmt_attempts.where(
                TaskDispatchAttempt.dispatch_session_id == dispatch_session.id
            )
        res_attempts = await self.attempt_repo.execute(stmt_attempts)
        raw_attempts = res_attempts.all()
        attempted_ids: Set[str] = {
            (row[0] if isinstance(row, (tuple, Row)) else row)
            for row in raw_attempts
            if (row[0] if isinstance(row, (tuple, Row)) else row) is not None
        }

        all_excluded.update(attempted_ids)
        return list(all_excluded)

    async def _validate_task_for_matching(self, task: Task) -> Optional[TaskLocation]:
        """Validates if task is fit for matching (has service_id and valid TaskLocation) and returns TaskLocation."""
        if not task.service_id:
            print(
                f"DEBUG [_fetch_and_filter]: No service_id on task {task.id}, returning empty"
            )
            return None

        stmt_loc = select(TaskLocation).where(TaskLocation.task_id == task.id).limit(1)
        res_loc = await self.task_location_repo.execute(stmt_loc)

        task_loc: Optional[TaskLocation] = res_loc.one_or_none()
        if not task_loc or task_loc.latitude is None or task_loc.longitude is None:
            print(
                f"DEBUG [_fetch_and_filter]: No task location found for task {task.id}"
            )
            return None

        print(
            f"DEBUG [_fetch_and_filter]: Task location: lat={task_loc.latitude}, lon={task_loc.longitude}"
        )
        return task_loc

    async def _fetch_and_filter_candidates(
        self,
        task: Task,
        excluded_provider_ids: Optional[List[str]] = None,
        dispatch_session: Optional[DispatchSession] = None,
    ) -> List[_ScoredCandidate]:
        """Discovers nearby providers, excludes already pinged/excluded candidates at DB level, scores
        them, and returns the top batch.

        Pagination vs. radius expansion: The DB queries candidates filtering out any already 
        attempted ones. If the batch of `limit_pool` candidates yields no eligible ones, we 
        try again at the same radius. The next DB query naturally returns the next batch since 
        the evaluated ones are added to the exclusion list. Once the DB returns fewer candidates 
        than the pool size, the radius expands.
        """
        task_loc = await self._validate_task_for_matching(task)
        if not task_loc:
            return []

        all_excluded_list = await self._get_excluded_provider_ids(
            task_id=task.id,
            excluded_provider_ids=excluded_provider_ids,
            dispatch_session=dispatch_session,
        )
        all_excluded: Set[str] = set(all_excluded_list)

        current_radius: float = (
            dispatch_session.search_radius_km
            if dispatch_session and dispatch_session.search_radius_km is not None
            else 10.0
        )
        max_radius: float = (
            dispatch_session.max_search_radius_km
            if dispatch_session and dispatch_session.max_search_radius_km is not None
            else 30.0
        )
        auto_expand: bool = (
            dispatch_session.auto_expand_radius
            if dispatch_session and dispatch_session.auto_expand_radius is not None
            else True
        )

        batch_size = max(
            1,
            dispatch_session.batch_size
            if (dispatch_session and dispatch_session.batch_size)
            else 5,
        )

        limit_pool = 50
        local_excluded = set(all_excluded_list)
        all_eligible_rows = []
        nearby_map = {}

        while True:
            nearby_results: List[NearbyProviderResult] = (
                await self.geo_service.search_nearby_providers(
                    latitude=task_loc.latitude,
                    longitude=task_loc.longitude,
                    radius_km=current_radius,
                    limit=limit_pool,
                    excluded_provider_ids=list(local_excluded),
                    service_id=task.service_id,
                )
            )

            if not nearby_results:
                # No more candidates at this radius. Expand radius.
                if auto_expand and current_radius < max_radius:
                    current_radius = min(current_radius + 10.0, max_radius)
                    continue
                else:
                    break

            for r in nearby_results:
                if r.provider_id and r.distance_km is not None:
                    nearby_map[r.provider_id] = r.distance_km

            provider_ids = [r.provider_id for r in nearby_results if r.provider_id]
            
            # Exclude them for the next query so we don't fetch them again if we loop
            local_excluded.update(provider_ids)
            if provider_ids:
                assert task.service_id, "Task service id must not be null"
                rows = await self.__get_eligible(
                    provider_ids=provider_ids,
                    service_id=task.service_id,
                )
                all_eligible_rows.extend(rows)

            if len(all_eligible_rows) >= batch_size:
                # We have enough candidates
                break
                
            # If we fetched fewer than limit_pool, we've exhausted all online providers at this radius.
            if len(nearby_results) < limit_pool:
                if auto_expand and current_radius < max_radius:
                    current_radius = min(current_radius + 10.0, max_radius)
                else:
                    break
            else:
                # We fetched limit_pool, but didn't get enough eligible.
                # Loop again at the SAME radius. The ineligible ones are now in local_excluded,
                # so the DB will return the next closest `limit_pool` providers.
                continue

        await self._update_search_progress(
            dispatch_session=dispatch_session,
            current_radius=current_radius,
        )

        # Debug: check each provider individually to see which filter is eliminating them
        # NOTE: This query is strictly gated behind IS_LOCAL for dev diagnostics
        # and MUST NOT be removed from the IS_LOCAL condition or used in production paths.
        if not all_eligible_rows and local_excluded and IS_LOCAL:
            debug_ids = [pid for pid in local_excluded if pid not in all_excluded_list]
            if debug_ids:
                stmt_debug = (
                    select(User)
                    .where(col(User.id).in_(debug_ids))
                    # pyrefly: ignore [bad-argument-type]
                    .options(selectinload(User.provider_profile))
                )
                users = await self.user_repo.session.scalars(stmt_debug)
                for u in users:
                    profile = u.provider_profile
                    print(
                        f"  -> {u.id}: user.is_active={u.is_active}, "
                        f"profile.status={getattr(profile, 'status', None)}, "
                        f"profile.is_online={getattr(profile, 'is_online', None)}, "
                        f"profile.duty_status={getattr(profile, 'duty_status', None)}"
                    )

        return self.__score_candidates(
            rows=all_eligible_rows,
            nearby_map=nearby_map,
            batch_size=batch_size,
            current_radius=current_radius,
        )

    async def _send_batch_ping_notification(
        self,
        user_ids: List[str],
        task: Task,
        dispatch_session_id: str,
        offered_payout: float,
        expires_at: Optional[str] = None,
    ) -> None:
        """Sends a single notification to all candidate providers in the batch at once."""
        payout_fmt = (
            f"₦{offered_payout:,.2f}" if offered_payout > 0 else "offered price"
        )
        if self.ping_duration >= 60 and self.ping_duration % 60 == 0:
            mins = self.ping_duration // 60
            time_str = f"{mins} minute" if mins == 1 else f"{mins} minutes"
        else:
            time_str = f"{self.ping_duration} seconds"

        await self.notification_service.notify(
            recepients=user_ids,
            title="New Task Offer",
            body=(
                f"You have a new task offer '{task.title}' for {payout_fmt}. "
                f"Tap to respond within {time_str}!"
            ),
            type=NotificationType.JOB_PING,
            channels=["PUSH", "IN_APP"],
            data={
                "task_id": task.id,
                "dispatch_session_id": dispatch_session_id,
                "offered_payout": offered_payout,
                "expires_at": expires_at,
                "type": "JOB_PING",
            },
        )

    async def _send_cancellation_notification(
        self,
        customer_id: str,
        task: Task,
        session_id: str,
    ) -> None:
        """Sends a task cancellation notification to customer when candidate pool is exhausted."""
        await self.notification_service.notify(
            recepients=[customer_id],
            title="No Providers Available",
            body=f"We couldn't find an available provider for your task '{task.title}'. The task has been cancelled.",
            type=NotificationType.TASK_CANCELLED,
            channels=["PUSH", "IN_APP"],
            data={
                "task_id": task.id,
                "session_id": session_id,
                "type": "TASK_CANCELLED",
                "reason": "no_candidates_available",
            },
        )

    async def _dispatch_to_candidate(
        self,
        candidate: _ScoredCandidate,
        task: Task,
        dispatch_session_id: str,
        sequence_order: int,
        ping_duration: Optional[int] = None,
    ) -> Optional[TaskDispatchAttempt]:
        """Creates dispatch attempt, updates provider duty status to ON_DISPATCH, and sends ping notification."""
        effective_ping_duration = (
            ping_duration if ping_duration is not None else self.ping_duration
        )
        now = lagos_now()
        offered_payout = task.provider_payout or 0.0

        # 1. Atomically lock the task row to verify it's still SEARCHING
        stmt_task = select(Task.status).where(Task.id == task.id).with_for_update()
        res_task = await self.task_repo.execute(stmt_task)
        current_status = res_task.one_or_none()
        if current_status != TaskStatus.SEARCHING:
            logger.info(f"MatchingEngine: Task {task.id} is no longer SEARCHING. Aborting dispatch attempt.")
            return None

        # 2. Atomically update provider duty status to ON_DISPATCH only if still ONLINE_AVAILABLE
        stmt_update = (
            update(ProviderProfile)
            .where(
                col(ProviderProfile.user_id) == candidate.user_id,
                ProviderProfile.duty_status == DutyStatus.ONLINE_AVAILABLE,  # type: ignore
            )
            .values(duty_status=DutyStatus.ON_DISPATCH)
        )
        res_update = await self.provider_profile_repo.execute(stmt_update)
        if res_update.rowcount == 0:
            logger.debug(
                f"MatchingEngine: Candidate {candidate.user_id} is no longer ONLINE_AVAILABLE (lost race). Skipping."
            )
            return None

        attempt = TaskDispatchAttempt(
            dispatch_session_id=dispatch_session_id,
            task_id=task.id,
            provider_id=candidate.user_id,
            sequence_order=sequence_order,
            match_score=candidate.score,
            offered_payout=offered_payout,
            pinged_at=now,
            expires_at=now + timedelta(seconds=effective_ping_duration),
            status=DispatchAttemptStatus.PENDING,
        )
        await self.attempt_repo.add(attempt)
        return attempt

    async def _handle_pool_exhaustion(
        self,
        task: Task,
        dispatch_session: DispatchSession,
    ) -> bool:
        """Handles exhausted candidate pool by expiring dispatch session, resetting provider duty statuses, and cancelling task."""
        dispatch_session.status = DispatchSessionStatus.EXPIRED
        dispatch_session.updated_at = lagos_now()
        await self.session_repo.add(dispatch_session)

        # Atomically cancel the task ONLY if it is still SEARCHING
        stmt_cancel_task = (
            update(Task)
            .where(
                col(Task.id) == task.id,
                Task.status == TaskStatus.SEARCHING,  # type: ignore
            )
            .values(
                status=TaskStatus.CANCELLED,
                cancellation_reason="No available provider accepted the task offer.",
                updated_at=lagos_now(),
            )
        )
        res_cancel = await self.task_repo.execute(stmt_cancel_task)
        
        if res_cancel.rowcount == 0:
            msg = f"MatchingEngine: Task {task.id} was accepted before pool exhaustion could complete. Skipping cancellation."
            print(f"DEBUG [_handle_pool_exhaustion]: {msg}")
            await self.system_logger.info(msg, source=_LOG_SOURCE)
            return False

        # 1. Update any PENDING attempts for this task to TIMEOUT directly via SQL UPDATE query
        stmt_update_attempts = (
            update(TaskDispatchAttempt)
            .where(
                TaskDispatchAttempt.task_id == task.id,  # type: ignore
                TaskDispatchAttempt.status == DispatchAttemptStatus.PENDING,  # type: ignore
            )
            .values(
                status=DispatchAttemptStatus.TIMEOUT,
                responded_at=lagos_now(),
            )
        )
        await self.attempt_repo.execute(stmt_update_attempts)

        # 2. Reset duty status of all pinged providers back to ONLINE_AVAILABLE via bulk update by user_id
        stmt_attempts = select(TaskDispatchAttempt.provider_id).where(
            TaskDispatchAttempt.task_id == task.id,
            TaskDispatchAttempt.dispatch_session_id == dispatch_session.id
        )
        res_attempts = await self.attempt_repo.execute(stmt_attempts)
        raw_attempts = res_attempts.all()
        user_ids = [
            (row[0] if isinstance(row, (tuple, Row)) else row)
            for row in raw_attempts
            if (row[0] if isinstance(row, (tuple, Row)) else row) is not None
        ]
        if user_ids:
            stmt_update_profiles = (
                update(ProviderProfile)
                .where(
                    ProviderProfile.user_id.in_(user_ids),  # type: ignore
                    ProviderProfile.duty_status == DutyStatus.ON_DISPATCH,  # type: ignore
                )
                .values(duty_status=DutyStatus.ONLINE_AVAILABLE)
            )
            await self.provider_profile_repo.execute(stmt_update_profiles)

        if task.customer_id:
            await self._send_cancellation_notification(
                customer_id=task.customer_id,
                task=task,
                session_id=dispatch_session.id,
            )
        return False

    async def _recover_stale_attempts(self, task: Task) -> None:
        """Local Recovery: clean up stale PENDING attempts from previous iterations."""
        stmt_stale = select(TaskDispatchAttempt.id, TaskDispatchAttempt.provider_id).where(
            TaskDispatchAttempt.task_id == task.id,
            TaskDispatchAttempt.status == DispatchAttemptStatus.PENDING,  # type: ignore
            col(TaskDispatchAttempt.expires_at) <= lagos_now()
        )
        res_stale = await self.attempt_repo.execute(stmt_stale)
        stale_records = res_stale.all()
        if stale_records:
            stale_attempt_ids = [r[0] for r in stale_records]
            stale_provider_ids = [r[1] for r in stale_records]
            
            # Timeout stale attempts
            await self.attempt_repo.execute(
                update(TaskDispatchAttempt)
                .where(col(TaskDispatchAttempt.id).in_(stale_attempt_ids))
                .values(status=DispatchAttemptStatus.TIMEOUT, responded_at=lagos_now())
            )
            # Release stranded providers
            await self.provider_profile_repo.execute(
                update(ProviderProfile)
                .where(
                    col(ProviderProfile.user_id).in_(stale_provider_ids),
                    ProviderProfile.duty_status == DutyStatus.ON_DISPATCH  # type: ignore
                )
                .values(duty_status=DutyStatus.ONLINE_AVAILABLE)
            )
            await self.provider_profile_repo.session.commit()
            logger.info(f"MatchingEngine: Recovered {len(stale_attempt_ids)} stale attempts for task {task.id}")

    async def _schedule_next_run(self, dispatch_session_id: str, countdown: int) -> None:
        """Schedules the next MatchingEngine.run() step via Celery after `countdown` seconds."""
        from app.features.tasks.celery.dispatch import execute_matching_engine_task

        # pyrefly: ignore [not-callable]
        execute_matching_engine_task.apply_async(
            args=[dispatch_session_id],
            countdown=countdown,
        )  # type: ignore

    async def run(self) -> bool:
        """Executes a single step of candidate discovery, ranking, and notification ping for a task dispatch session.

        WHAT THIS METHOD DOES (STEP-BY-STEP IN SIMPLE TERMS):
        ----------------------------------------------------
        1. SESSION VERIFICATION:
           Loads the active `DispatchSession` by ID. If the session does not exist or is no longer
           in `SEARCHING` status (e.g. already assigned or expired), it stops immediately.

        2. OPTIMISTIC CONCURRENCY LOCK (LOCK VERSION INCREMENT):
           Uses SQL optimistic concurrency locking to ensure ONLY ONE worker processes this step.
           It attempts to update `lock_version` from N to N+1 in the database. If another worker
           concurrently modified the session, `rowcount` will be 0 and this engine instance exits
           cleanly. `lock_version` is a pure concurrency guard — it is never used for pagination
           and is never touched anywhere outside this step.

        3. TASK VALIDATION:
           Loads the corresponding `Task`. Verifies that the task is still in `SEARCHING` status.
           If the task was cancelled, assigned, or deleted, it halts execution.

        4. CANDIDATE DISCOVERY & RANKING:
           - Spatial Search: Uses PostGIS to find all service providers within a 10km radius of the task location.
           - Eligibility Filtering: Filters providers in DB by: active user, verified KYC status, linked to required service ID, duty status = ONLINE_AVAILABLE, and availability schedule.
           - Quality Scoring: Scores each eligible provider using acceptance rate (30%), average ratings (25%), credibility score (25%), minus proximity distance penalty (20%).
           - Next Batch Selection: Selects top unattempted providers matching `batch_size` (e.g., top 1-5 providers who haven't been pinged yet).

        5. EXHAUSTION HANDLING (EMPTY BATCH):
           If no eligible unattempted providers remain, delegates to `_handle_pool_exhaustion`.

        6. DISPATCH PINGS & DUTY STATUS UPDATE:
           For each candidate in the selected batch, delegates to `_dispatch_to_candidate`.

        7. RECURSIVE DISPATCH ITERATION SCHEDULING:
           Schedules a background Celery task (`execute_matching_engine_task`) after a countdown of
           `ping_duration + 120` seconds (300s at the default 180s ping duration). If no provider
           accepts within that window, Celery triggers the next engine step automatically.

        Returns:
            bool: True if a batch of candidates was successfully pinged; False if stopped or pool exhausted.
        """
        print(
            f"\nDEBUG [MatchingEngine.run]: ===== STARTING RUN FOR SESSION_ID={self.session_id} ====="
        )
        logger.debug(f"MatchingEngine.run starting for session {self.session_id}")

        # 1. Fetch dispatch session
        dispatch_session = await self.session_repo.get(self.session_id)
        if (
            not dispatch_session
            or dispatch_session.status != DispatchSessionStatus.SEARCHING
        ):
            status_val = dispatch_session.status if dispatch_session else None
            print(
                f"DEBUG [MatchingEngine.run]: Dispatch session validation failed. exists={bool(dispatch_session)}, status={status_val}"
            )
            msg = f"MatchingEngine: Session {self.session_id} not searching (status={status_val}). Exiting."
            logger.info(msg)
            return False

        # 2. Optimistic concurrency check & lock_version increment (pure lock — no pagination semantics)
        lock_version = dispatch_session.lock_version
        stmt_opt = (
            update(DispatchSession)
            .where(
                DispatchSession.id == self.session_id,  # type: ignore
                DispatchSession.lock_version == lock_version,  # type: ignore
                DispatchSession.status == DispatchSessionStatus.SEARCHING,  # type: ignore
            )
            .values(
                lock_version=lock_version + 1,
                updated_at=lagos_now(),
            )
        )
        res_opt = await self.session_repo.execute(stmt_opt)
        if res_opt.rowcount == 0:
            msg = f"MatchingEngine: Optimistic locking conflict for session {self.session_id} (lock_version={lock_version}). Exiting."
            print(msg)
            await self.system_logger.warn(msg, source=_LOG_SOURCE)
            return False

        # Get the refreshed dispatch session from database
        await self.session_repo.refresh(dispatch_session)
        print(
            f"DEBUG [MatchingEngine.run]: Concurrency check passed. Lock version is now {dispatch_session.lock_version}"
        )
        logger.debug(f"MatchingEngine: Session {self.session_id} lock version advanced to {dispatch_session.lock_version}")

        # 3. Load associated task
        task = await self.task_repo.get(dispatch_session.task_id)
        if not task or task.status != TaskStatus.SEARCHING:
            msg = f"MatchingEngine: Task {dispatch_session.task_id} not in SEARCHING status. Halting engine."
            logger.info(msg)
            return False

        # Read and merge session-level excluded_provider_ids with constructor exclusions
        combined_excluded: Set[str] = set(self.excluded_provider_ids)
        if dispatch_session.excluded_provider_ids:
            combined_excluded.update(dispatch_session.excluded_provider_ids)
        excluded_ids_list = list(combined_excluded)

        # 4. Local Recovery: clean up stale PENDING attempts from previous iterations
        await self._recover_stale_attempts(task)

        print(
            f"DEBUG [MatchingEngine.run]: Calling _fetch_and_filter_candidates for task_id={task.id}"
        )
        batch = await self._fetch_and_filter_candidates(
            task,
            excluded_provider_ids=excluded_ids_list,
            dispatch_session=dispatch_session,
        )
        batch_user_ids = [c.user_id for c in batch]
        print(
            f"DEBUG [MatchingEngine.run]: _fetch_and_filter_candidates returned {len(batch)} candidates for batch: {batch_user_ids}"
        )

        candidate_summary = [
            {"user_id": c.user_id, "score": c.score, "distance_km": c.distance_km}
            for c in batch
        ]

        logger.debug(f"MatchingEngine: Selected batch of {len(batch)} candidate(s) for task {task.id} (lock_version={lock_version})")

        # 5. Handle empty batch (candidate pool exhausted)
        if not batch:
            print(
                f"DEBUG [MatchingEngine.run]: Calling _handle_pool_exhaustion because batch is empty"
            )
            exhausted = await self._handle_pool_exhaustion(task, dispatch_session)
            print(
                f"DEBUG [MatchingEngine.run]: _handle_pool_exhaustion returned {exhausted}"
            )
            msg = f"MatchingEngine: No candidates left for task {task.id}. Session {dispatch_session.id} EXPIRED."
            print(f"DEBUG [MatchingEngine.run]: {msg}")
            await self.system_logger.info(
                msg,
                source=_LOG_SOURCE,
                metadata={"task_id": task.id, "session_id": dispatch_session.id},
            )
            return exhausted

        # 6. Issue dispatch attempt pings for candidates in batch
        ping_duration = self.ping_duration
        stmt_attempts_count = select(func.max(TaskDispatchAttempt.sequence_order)).where(
            TaskDispatchAttempt.dispatch_session_id == dispatch_session.id
        )
        res_count = await self.attempt_repo.execute(stmt_attempts_count)
        raw_count = res_count.one_or_none()
        max_seq = (raw_count[0] if isinstance(raw_count, (tuple, Row)) else raw_count)
        seq_start = (max_seq or 0) + 1

        attempts: List[TaskDispatchAttempt] = []
        dispatched_user_ids: List[str] = []
        for candidate in batch:
            print(
                f"DEBUG [MatchingEngine.run]: Calling _dispatch_to_candidate for candidate (user_id={candidate.user_id})"
            )
            attempt = await self._dispatch_to_candidate(
                candidate=candidate,
                task=task,
                dispatch_session_id=dispatch_session.id,
                sequence_order=seq_start + len(attempts),
                ping_duration=ping_duration,
            )
            if not attempt:
                continue

            attempts.append(attempt)
            dispatched_user_ids.append(candidate.user_id)
            print(
                f"DEBUG [MatchingEngine.run]: _dispatch_to_candidate returned attempt_id={attempt.id}"
            )
            logger.debug(f"MatchingEngine: Dispatched ping attempt {attempt.id} to provider {candidate.user_id} (score={candidate.score:.2f})")

        if not attempts:
            msg = f"MatchingEngine: All {len(batch)} candidate(s) in batch lost duty_status race for task {task.id}. Scheduling immediate retry."
            print(f"DEBUG [MatchingEngine.run]: {msg}")
            await self.system_logger.warn(
                msg,
                source=_LOG_SOURCE,
                metadata={"task_id": task.id, "session_id": dispatch_session.id, "batch_size": len(batch)},
            )

            _RACE_RETRY_DELAY_SECONDS = 5
            await self._schedule_next_run(dispatch_session.id, _RACE_RETRY_DELAY_SECONDS)
            return False

        # Send a single notification to all successfully dispatched candidates in the batch
        user_ids = dispatched_user_ids
        offered_payout = task.provider_payout or 0.0
        last_expires_at = (
            attempts[-1].expires_at.isoformat()
            if attempts and attempts[-1].expires_at
            else None
        )
        
        try:
            await self._send_batch_ping_notification(
                user_ids=user_ids,
                task=task,
                dispatch_session_id=dispatch_session.id,
                offered_payout=offered_payout,
                expires_at=last_expires_at,
            )
        except Exception as e:
            logger.error(f"MatchingEngine: Failed to send batch ping notification for task {task.id}: {e}")

        # 7. Schedule next matching engine iteration via Celery task delay
        scheduled_delay = ping_duration + 120
        await self._schedule_next_run(dispatch_session.id, scheduled_delay)

        msg = f"MatchingEngine: Dispatched batch of {len(attempts)} candidate(s) for session {dispatch_session.id} (task={task.id}). Next iteration scheduled in {scheduled_delay}s."
        print(f"DEBUG [MatchingEngine.run]: {msg}")
        print(
            f"DEBUG [MatchingEngine.run]: ===== END RUN FOR SESSION_ID={self.session_id} =====\n"
        )
        logger.info(msg)
        return True
