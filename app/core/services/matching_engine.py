from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional, Set, Tuple

from sqlalchemy import update
from sqlmodel import select
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
from app.core.models.users import DutyStatus, KYCStatus, ProviderProfile, User, UserLocation
from app.core.repository import Repository
from app.core.services.availability_service import get_availability_service_manual
from app.core.services.logger_service import get_logger_service_manual
from app.core.services.provider_location import NearbyProviderResult, PostGISProviderLocationService
from app.core.utils.datetime_helper import lagos_now
from app.features.notifications.services import get_notification_service_manual


_LOG_SOURCE = "core.MatchingEngine"


@dataclass
class _ScoredCandidate:
    provider_id: str
    distance_km: float
    score: float


class MatchingEngine:
    """Ephemeral matching engine that advances a task dispatch session by one step.

    Design Principles:
    1. Ephemeral: Instantiated per execution step with session_id and AsyncSession.
    2. Stateful DB Concurrency: Validates status == SEARCHING and uses optimistic
       concurrency locking (current_batch field) to prevent duplicate dispatch execution.
    3. Spatial & Multi-factor Ranking: Discovers candidates via PostGIS/spatial index,
       applies DB eligibility filters, scores candidate quality, and pings in batches.
    """

    def __init__(
        self,
        session_id: str,
        db_session: AsyncSession,
        ping_duration: int = 180,
    ):
        self.session_id = session_id
        self.db_session = db_session
        self.ping_duration = ping_duration

        self.session_repo = Repository(DispatchSession, db_session)
        self.task_repo = Repository(Task, db_session)
        self.task_location_repo = Repository(TaskLocation, db_session)
        self.attempt_repo = Repository(TaskDispatchAttempt, db_session)
        self.provider_profile_repo = Repository(ProviderProfile, db_session)
        self.user_repo = Repository(User, db_session)

        location_repo = Repository(UserLocation, db_session)
        self.geo_service = PostGISProviderLocationService(
            location_repo=location_repo,
            provider_profile_repo=self.provider_profile_repo,
        )
        self.notification_service = get_notification_service_manual(db_session)
        self.availability_service = get_availability_service_manual(db_session)
        self.system_logger = get_logger_service_manual(db_session)

    async def _fetch_and_filter_candidates(
        self, task: Task
    ) -> List[Tuple[User, ProviderProfile, float]]:
        """Discovers nearby providers using spatial index and filters by DB eligibility."""
        if not task.service_id:
            print(f"DEBUG [_fetch_and_filter]: No service_id on task {task.id}, returning empty")
            return []

        stmt_loc = select(TaskLocation).where(TaskLocation.task_id == task.id).limit(1)
        res_loc = await self.task_location_repo.execute(stmt_loc)

        # Do not call scalar or none, result is already a scalar
        task_loc: Optional[TaskLocation] = res_loc.one_or_none()
        if not task_loc or task_loc.latitude is None or task_loc.longitude is None:
            print(f"DEBUG [_fetch_and_filter]: No task location found for task {task.id}")
            return []

        print(f"DEBUG [_fetch_and_filter]: Task location: lat={task_loc.latitude}, lon={task_loc.longitude}")

        nearby_results: List[NearbyProviderResult] = (
            await self.geo_service.search_nearby_providers(
                latitude=task_loc.latitude,
                longitude=task_loc.longitude,
                radius_km=10.0,
                limit=2000
            )
        )
        print(f"DEBUG [_fetch_and_filter]: Spatial search returned {len(nearby_results)} nearby providers")
        for r in nearby_results:
            print(f"  -> provider_id={r.provider_id}, distance_km={r.distance_km}, is_online={r.is_online}")

        if not nearby_results:
            print(f"DEBUG [_fetch_and_filter]: No nearby providers found within 10km radius")
            return []

        nearby_map = {
            r.provider_id: r.distance_km
            for r in nearby_results
            if r.provider_id and r.distance_km is not None
        }
        provider_ids = list(nearby_map.keys())
        print(f"DEBUG [_fetch_and_filter]: {len(provider_ids)} provider IDs from spatial search: {provider_ids}")

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
                ProviderServiceLink.service_id == task.service_id,
            )
        )

        target_time = task.scheduled_start_at if task.scheduled_start_at else lagos_now()
        stmt_eligibility = stmt_eligibility.where(
            ProviderProfile.is_online == True,  # noqa: E712
            ProviderProfile.duty_status == DutyStatus.ONLINE_AVAILABLE,
            # self.availability_service.get_availability_sql_condition(target_time),
        )

        res_eligibility = await self.provider_profile_repo.execute(stmt_eligibility)
        rows = res_eligibility.unique().all()
        print(f"DEBUG [_fetch_and_filter]: Eligibility query returned {len(rows)} rows")

        # Debug: check each provider individually to see which filter is eliminating them
        if not rows and provider_ids:
            for pid in provider_ids:
                u = await self.user_repo.get(pid)
                if not u:
                    print(f"  -> {pid}: User NOT FOUND in DB")
                    continue
                p = u.provider_profile
                print(f"  -> {pid}: is_active={u.is_active}, kyc_status={p.status if p else 'NO_PROFILE'}, "
                      f"is_online={p.is_online if p else 'N/A'}, duty_status={p.duty_status if p else 'N/A'}")

        eligible: List[Tuple[User, ProviderProfile, float]] = []
        for user, profile in rows:
            dist_km = nearby_map.get(user.id, 10.0)
            eligible.append((user, profile, dist_km))

        return eligible

    def _score_and_sort_candidates(
        self, candidates: List[Tuple[User, ProviderProfile, float]]
    ) -> List[_ScoredCandidate]:
        """Calculates multi-factor ranking scores and sorts candidates descending."""
        scored: List[_ScoredCandidate] = []
        for user, profile, dist_km in candidates:
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
                _ScoredCandidate(
                    provider_id=user.id,
                    distance_km=dist_km,
                    score=round(score, 2),
                )
            )

        scored.sort(key=lambda c: c.score, reverse=True)
        return scored

    async def _get_next_batch(
        self,
        scored_candidates: List[_ScoredCandidate],
        task_id: str,
        batch_size: int,
    ) -> List[_ScoredCandidate]:
        """Excludes candidates already pinged for the task and returns the next batch."""
        stmt_attempts = select(TaskDispatchAttempt).where(
            TaskDispatchAttempt.task_id == task_id
        )
        res_attempts = await self.attempt_repo.execute(stmt_attempts)
        existing_attempts = list(res_attempts.all())

        attempted_ids: Set[str] = {
            a.provider_id for a in existing_attempts if a.provider_id
        }
        unattempted = [c for c in scored_candidates if c.provider_id not in attempted_ids]

        return unattempted[:batch_size]

    async def _send_batch_ping_notification(
        self,
        provider_ids: List[str],
        task: Task,
        dispatch_session_id: str,
        offered_payout: float,
        expires_at: Optional[str] = None,
    ) -> None:
        """Sends a single notification to all candidate providers in the batch at once."""
        payout_fmt = f"₦{offered_payout:,.2f}" if offered_payout > 0 else "offered price"
        await self.notification_service.notify(
            recepients=provider_ids,
            title="New Task Offer",
            body=(
                f"You have a new task offer '{task.title}' for {payout_fmt}. "
                f"Tap to respond within 30 seconds!"
            ),
            type=NotificationType.JOB_PING,
            channels=["push", "in_app"],
            data={
                "task_id": task.id,
                "dispatch_session_id": dispatch_session_id,
                "offered_payout": offered_payout,
                "expires_at": expires_at,
                "type": "job_ping",
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
            channels=["push", "in_app"],
            data={
                "task_id": task.id,
                "session_id": session_id,
                "type": "task_cancelled",
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
    ) -> TaskDispatchAttempt:
        """Creates dispatch attempt, updates provider duty status to ON_DISPATCH, and sends ping notification."""
        effective_ping_duration = ping_duration if ping_duration is not None else self.ping_duration
        now = lagos_now()
        offered_payout = task.provider_payout or 0.0

        attempt = TaskDispatchAttempt(
            dispatch_session_id=dispatch_session_id,
            task_id=task.id,
            provider_id=candidate.provider_id,
            sequence_order=sequence_order,
            match_score=candidate.score,
            offered_payout=offered_payout,
            pinged_at=now,
            expires_at=now + timedelta(seconds=effective_ping_duration),
            status=DispatchAttemptStatus.PENDING,
        )
        await self.attempt_repo.add(attempt)

        # Set provider duty status to ON_DISPATCH
        stmt_prof = select(ProviderProfile).where(
            ProviderProfile.user_id == candidate.provider_id
        )
        res_prof = await self.provider_profile_repo.execute(stmt_prof)
        profile: Optional[ProviderProfile] = res_prof.one_or_none()
        if profile:
            profile.duty_status = DutyStatus.ON_DISPATCH
            await self.provider_profile_repo.add(profile)

        return attempt

    async def _handle_pool_exhaustion(
        self,
        task: Task,
        dispatch_session: DispatchSession,
    ) -> bool:
        """Handles exhausted candidate pool by expiring dispatch session and cancelling task."""
        dispatch_session.status = DispatchSessionStatus.EXPIRED
        dispatch_session.updated_at = lagos_now()
        await self.session_repo.add(dispatch_session)

        task.status = TaskStatus.CANCELLED
        await self.task_repo.add(task)

        if task.customer_id:
            await self._send_cancellation_notification(
                customer_id=task.customer_id,
                task=task,
                session_id=dispatch_session.id,
            )
        return False

    async def run(self) -> bool:
        """Executes a single step of candidate discovery, ranking, and notification ping for a task dispatch session.

        WHAT THIS METHOD DOES (STEP-BY-STEP IN SIMPLE TERMS):
        ----------------------------------------------------
        1. SESSION VERIFICATION:
           Loads the active `DispatchSession` by ID. If the session does not exist or is no longer
           in `SEARCHING` status (e.g. already assigned or expired), it stops immediately.

        2. OPTIMISTIC CONCURRENCY LOCK (BATCH INCREMENT):
           Uses SQL optimistic concurrency locking to ensure ONLY ONE worker processes this batch step.
           It attempts to update `current_batch` from N to N+1 in the database. If another worker
           concurrently modified the session, `rowcount` will be 0 and this engine instance exits cleanly.

        3. TASK VALIDATION:
           Loads the corresponding `Task`. Verifies that the task is still in `SEARCHING` status.
           If the task was cancelled, assigned, or deleted, it halts execution.

        4. CANDIDATE DISCOVERY & RANKING:
           - Spatial Search: Uses PostGIS to find all service providers within a 10km radius of the task location.
           - Eligibility Filtering: Filters providers in DB by: active user, verified KYC status, linked to required service ID, duty status = ONLINE_AVAILABLE, and availability schedule.
           - Quality Scoring: Scores each eligible provider using ratings (30%), completed tasks (20%), credibility score (20%), acceptance rate (15%), and proximity distance (15%).
           - Next Batch Selection: Selects top unattempted providers matching `batch_size` (e.g., top 1-5 providers who haven't been pinged yet).

        5. EXHAUSTION HANDLING (EMPTY BATCH):
           If no eligible unattempted providers remain, delegates to `_handle_pool_exhaustion`.

        6. DISPATCH PINGS & DUTY STATUS UPDATE:
           For each candidate in the selected batch, delegates to `_dispatch_to_candidate`.

        7. RECURSIVE DISPATCH ITERATION SCHEDULING:
           Schedules a background Celery task (`execute_matching_engine_task`) with a 3-minute countdown delay.
           If no provider accepts within 3 mins, Celery triggers the next engine step automatically.

        Returns:
            bool: True if a batch of candidates was successfully pinged; False if stopped or pool exhausted.
        """
        print(f"\nDEBUG [MatchingEngine.run]: ===== STARTING RUN FOR SESSION_ID={self.session_id} =====")
        await self.system_logger.info(
            f"MatchingEngine.run starting for session {self.session_id}",
            source=_LOG_SOURCE,
            metadata={"session_id": self.session_id},
        )

        # 1. Fetch dispatch session
        dispatch_session = await self.session_repo.get(self.session_id)
        if not dispatch_session or dispatch_session.status != DispatchSessionStatus.SEARCHING:
            print(f"DEBUG [MatchingEngine.run]: Dispatch session validation failed. exists={bool(dispatch_session)}, status={getattr(dispatch_session, 'status', None)}")
            msg = f"MatchingEngine: Session {self.session_id} not searching (status={getattr(dispatch_session, 'status', None)}). Exiting."
            print(msg)
            await self.system_logger.info(msg, source=_LOG_SOURCE)
            return False

        # 2. Optimistic concurrency check & current_batch increment
        batch_num = dispatch_session.current_batch
        stmt_opt = (
            update(DispatchSession)
            .where(
                DispatchSession.id == self.session_id,  # type: ignore
                DispatchSession.current_batch == batch_num,  # type: ignore
                DispatchSession.status == DispatchSessionStatus.SEARCHING,  # type: ignore
            )
            .values(
                current_batch=batch_num + 1,
                updated_at=lagos_now(),
            )
        )
        res_opt = await self.session_repo.execute(stmt_opt)
        if getattr(res_opt, "rowcount", 0) == 0:
            msg = f"MatchingEngine: Optimistic locking conflict for session {self.session_id} (batch={batch_num}). Exiting."
            print(msg)
            await self.system_logger.warn(msg, source=_LOG_SOURCE)
            return False

        await self.session_repo.refresh(dispatch_session)
        print(f"DEBUG [MatchingEngine.run]: Concurrency check passed. Current batch is now {dispatch_session.current_batch}")
        await self.system_logger.info(
            f"MatchingEngine: Session {self.session_id} batch counter advanced to {dispatch_session.current_batch}",
            source=_LOG_SOURCE,
            metadata={"session_id": self.session_id, "current_batch": dispatch_session.current_batch},
        )

        # 3. Load associated task
        task = await self.task_repo.get(dispatch_session.task_id)
        if not task or task.status != TaskStatus.SEARCHING:
            msg = f"MatchingEngine: Task {dispatch_session.task_id} not in SEARCHING status. Halting engine."
            print(msg)
            await self.system_logger.info(msg, source=_LOG_SOURCE)
            return False

        # 4. Fetch, score, and select batch of candidates
        print(f"DEBUG [MatchingEngine.run]: Calling _fetch_and_filter_candidates for task_id={task.id}")
        candidates = await self._fetch_and_filter_candidates(task)
        print(f"DEBUG [MatchingEngine.run]: _fetch_and_filter_candidates returned {len(candidates)} eligible candidates")
        await self.system_logger.info(
            f"MatchingEngine: Discovered {len(candidates)} candidate(s) passing spatial & eligibility filters for task {task.id}",
            source=_LOG_SOURCE,
            metadata={"task_id": task.id, "eligible_candidates_count": len(candidates)},
        )

        print(f"DEBUG [MatchingEngine.run]: Calling _score_and_sort_candidates")
        scored_candidates = self._score_and_sort_candidates(candidates)
        print(f"DEBUG [MatchingEngine.run]: _score_and_sort_candidates returned {len(scored_candidates)} scored candidates")
        
        batch_size = max(1, dispatch_session.batch_size or 1)
        print(f"DEBUG [MatchingEngine.run]: Calling _get_next_batch with batch_size={batch_size}")
        batch = await self._get_next_batch(scored_candidates, task.id, batch_size)
        print(f"DEBUG [MatchingEngine.run]: _get_next_batch returned {len(batch)} candidates for batch: {[c.provider_id for c in batch]}")

        candidate_summary = [
            {"provider_id": c.provider_id, "score": c.score, "distance_km": c.distance_km}
            for c in batch
        ]
        await self.system_logger.info(
            f"MatchingEngine: Selected batch of {len(batch)} candidate(s) for task {task.id} (batch_num={batch_num})",
            source=_LOG_SOURCE,
            metadata={
                "task_id": task.id,
                "batch_size": len(batch),
                "candidates": candidate_summary,
            },
        )

        # 5. Handle empty batch (candidate pool exhausted)
        if not batch:
            print(f"DEBUG [MatchingEngine.run]: Calling _handle_pool_exhaustion because batch is empty")
            exhausted = await self._handle_pool_exhaustion(task, dispatch_session)
            print(f"DEBUG [MatchingEngine.run]: _handle_pool_exhaustion returned {exhausted}")
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
        stmt_attempts_count = select(TaskDispatchAttempt).where(
            TaskDispatchAttempt.task_id == task.id
        )
        res_count = await self.attempt_repo.execute(stmt_attempts_count)
        seq_start = len(list(res_count.all())) + 1

        attempts: List[TaskDispatchAttempt] = []
        for idx, candidate in enumerate(batch):
            print(f"DEBUG [MatchingEngine.run]: Calling _dispatch_to_candidate for candidate {idx+1}/{len(batch)} (provider_id={candidate.provider_id})")
            attempt = await self._dispatch_to_candidate(
                candidate=candidate,
                task=task,
                dispatch_session_id=dispatch_session.id,
                sequence_order=seq_start + idx,
                ping_duration=ping_duration,
            )
            attempts.append(attempt)
            print(f"DEBUG [MatchingEngine.run]: _dispatch_to_candidate returned attempt_id={attempt.id}")
            await self.system_logger.info(
                f"MatchingEngine: Dispatched ping attempt {attempt.id} to provider {candidate.provider_id} (score={candidate.score:.2f})",
                source=_LOG_SOURCE,
                metadata={
                    "attempt_id": attempt.id,
                    "task_id": task.id,
                    "provider_id": candidate.provider_id,
                    "score": candidate.score,
                    "expires_at": attempt.expires_at.isoformat() if attempt.expires_at else None,
                },
            )

        # Send a single notification to all candidates in the batch at once
        # instead of one notification per candidate.
        provider_ids = [c.provider_id for c in batch]
        offered_payout = task.provider_payout or 0.0
        last_expires_at = attempts[-1].expires_at.isoformat() if attempts and attempts[-1].expires_at else None
        await self._send_batch_ping_notification(
            provider_ids=provider_ids,
            task=task,
            dispatch_session_id=dispatch_session.id,
            offered_payout=offered_payout,
            expires_at=last_expires_at,
        )

        # 7. Schedule next matching engine iteration via Celery task delay
        from app.features.tasks.celery.dispatch import execute_matching_engine_task

        # pyrefly: ignore [not-callable]
        execute_matching_engine_task.apply_async(
            args=[dispatch_session.id],
            countdown=ping_duration + 60, # Add 60 seconds for delay
        )

        msg = f"MatchingEngine: Dispatched batch of {len(batch)} candidate(s) for session {dispatch_session.id} (task={task.id}). Next iteration scheduled in {ping_duration + 60}s."
        print(f"DEBUG [MatchingEngine.run]: {msg}")
        print(f"DEBUG [MatchingEngine.run]: ===== END RUN FOR SESSION_ID={self.session_id} =====\n")
        await self.system_logger.info(
            msg,
            source=_LOG_SOURCE,
            metadata={
                "session_id": dispatch_session.id,
                "task_id": task.id,
                "batch_count": len(batch),
                "countdown_seconds": ping_duration,
            },
        )
        return True
