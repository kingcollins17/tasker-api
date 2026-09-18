import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from geoalchemy2 import Geography
from sqlalchemy import Row, cast, func, update
from sqlalchemy.orm import selectinload
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import IS_LOCAL
from app.core.logging import logger
from app.core.models.notifications import NotificationType
from app.core.models.services import ProviderServiceLink, Service
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
    UserStats,
)
from app.core.services.dispatch_policy import DispatchPolicy
from app.core.services.logger_service import (
    LoggerService,
    get_logger_service_manual,
)
from app.core.services.geo_service import (
    GeoService,
    NearbyProviderResult,
)
from app.core.utils.currency import to_naira
from app.core.utils.datetime_helper import lagos_now
from app.features.notifications.notification_service import (
    NotificationService,
    get_notification_service_manual,
)

_LOG_SOURCE = "core.MatchingEngine"


def _debug_log(message: str, data: Any = None) -> None:
    """Prints debug data directly to console/terminal to trace matching engine execution flow."""
    if data is not None:
        print(f"[MATCHING_ENGINE_DEBUG] {message}: {data}")
    else:
        print(f"[MATCHING_ENGINE_DEBUG] {message}")


@dataclass
class ScoredCandidate:
    user_id: str
    distance_km: float
    score: float


# Alias for backwards compatibility
_ScoredCandidate = ScoredCandidate


@dataclass
class EligibleCandidates:
    rows: List[Tuple[User, ProviderProfile]]
    nearby_map: Dict[str, float]


@dataclass
class MatchingResult:
    matched: bool
    candidates_count: int
    attempts_created: int
    reason: Optional[str] = None


class CandidateFetcher:
    """Handles candidate location validation, PostGIS discovery, and single-query eligibility matching."""

    def __init__(
        self,
        session: AsyncSession,
        geo_service: GeoService,
        exclude_previous_sessions: bool = True,
        excluded_provider_ids: Optional[List[str]] = None,
    ):
        self.session = session
        self.geo_service = geo_service
        self.exclude_previous_sessions = exclude_previous_sessions
        self.excluded_provider_ids = (
            list(excluded_provider_ids) if excluded_provider_ids else []
        )

    async def get_excluded_ids(
        self,
        # task_id: str,
        excluded_provider_ids: Optional[List[str]] = None,
        # dispatch_session: Optional[DispatchSession] = None,
    ) -> List[str]:
        """Combines explicit excluded provider IDs.
        Note: DB attempt exclusions are handled directly in PostgreSQL via NOT EXISTS in GeoService.
        """
        all_excluded: Set[str] = set(self.excluded_provider_ids)
        if excluded_provider_ids:
            all_excluded.update(excluded_provider_ids)
        return list(all_excluded)

    async def validate_task(self, task: Task) -> Optional[TaskLocation]:
        """Validates if task is fit for matching and returns TaskLocation without extra DB queries if loaded."""
        if not task.service_id:
            _debug_log(f"validate_task failed for task {task.id}: missing service_id")
            return None

        if task.locations:
            for loc in task.locations:
                if loc.latitude is not None and loc.longitude is not None:
                    _debug_log(
                        f"validate_task found loaded location for task {task.id}",
                        f"lat={loc.latitude}, lng={loc.longitude}",
                    )
                    return loc

        stmt_loc = (
            select(TaskLocation).where(col(TaskLocation.task_id) == task.id).limit(1)
        )
        res_loc = await self.session.exec(stmt_loc)
        task_loc: Optional[TaskLocation] = res_loc.one_or_none()
        if not task_loc or task_loc.latitude is None or task_loc.longitude is None:
            _debug_log(
                f"validate_task failed for task {task.id}: location DB query returned None or missing coordinates"
            )
            return None

        _debug_log(
            f"validate_task fetched location from DB for task {task.id}",
            f"lat={task_loc.latitude}, lng={task_loc.longitude}",
        )
        return task_loc

    async def fetch(
        self,
        task: Task,
        excluded_provider_ids: Optional[List[str]] = None,
        dispatch_session: Optional[DispatchSession] = None,
    ) -> List[NearbyProviderResult]:
        """Discovers nearby eligible providers in a single PostGIS DB query with NOT EXISTS exclusion."""
        _debug_log(
            f"fetch starting candidate discovery for task={task.id}, service_id={task.service_id}"
        )
        task_loc = await self.validate_task(task)
        if not task_loc or task_loc.latitude is None or task_loc.longitude is None:
            _debug_log(f"fetch aborted for task={task.id}: invalid task location")
            return []

        explicit_excluded = await self.get_excluded_ids(
            # task_id=task.id,
            excluded_provider_ids=excluded_provider_ids,
            # dispatch_session=dispatch_session,
        )
        _debug_log(
            f"fetch combined excluded provider count={len(explicit_excluded)}",
            explicit_excluded,
        )

        current_radius: float = (
            dispatch_session.search_radius_km
            if dispatch_session and dispatch_session.search_radius_km is not None
            else 10.0
        )

        batch_size = max(
            1,
            (
                dispatch_session.batch_size
                if (dispatch_session and dispatch_session.batch_size)
                else 5
            ),
        )

        assert task.service_id, "Task service id must not be null"

        target_point = func.ST_SetSRID(
            func.ST_MakePoint(
                task_loc.longitude,
                task_loc.latitude,
            ),
            4326,
        )
        distance_m_expr = func.ST_Distance(
            cast(UserLocation.last_known_location, Geography),
            cast(target_point, Geography),
        )

        dispatch_session_id = dispatch_session.id if dispatch_session else None

        stmt = (
            select(  # type: ignore
                UserLocation,
                ProviderProfile,
                (distance_m_expr / 1000.0).label("distance_km"),
                col(UserStats.acceptance_rate_30d),
                col(UserStats.average_ratings),
                col(UserStats.credibility_score),
            )
            .join(
                User,
                col(User.id) == col(UserLocation.user_id),
            )
            .join(
                ProviderProfile,
                col(ProviderProfile.user_id) == col(UserLocation.user_id),
            )
            .join(
                ProviderServiceLink,
                col(ProviderServiceLink.provider_id) == col(ProviderProfile.user_id),  # type: ignore
            )
            .join(
                Service,
                col(Service.id) == col(ProviderServiceLink.service_id),
            )
            .outerjoin(
                UserStats,
                col(UserStats.user_id) == col(User.id),
            )
            .where(
                col(UserLocation.last_known_location) != None,  # noqa: E711
                col(User.is_active) == True,  # noqa: E712
                col(ProviderProfile.kyc_status) == KYCStatus.VERIFIED,
                col(ProviderProfile.is_online) == True,  # noqa: E712
                col(ProviderProfile.duty_status) == DutyStatus.ONLINE_AVAILABLE,
                col(ProviderServiceLink.service_id) == task.service_id,
                func.coalesce(col(UserStats.current_tier), 1) >= col(Service.min_tier_required),
                func.ST_DWithin(
                    cast(UserLocation.last_known_location, Geography),
                    cast(target_point, Geography),
                    current_radius * 1000.0,
                ),
            )
        )

        subq = select(1).where(
            col(TaskDispatchAttempt.task_id) == task.id,
            col(TaskDispatchAttempt.provider_id) == col(UserLocation.user_id),
        )
        if not self.exclude_previous_sessions and dispatch_session_id:
            subq = subq.where(
                col(TaskDispatchAttempt.dispatch_session_id) == dispatch_session_id
            )
        stmt = stmt.where(~subq.exists())

        if explicit_excluded:
            stmt = stmt.where(~col(UserLocation.user_id).in_(explicit_excluded))

        stmt = stmt.order_by(distance_m_expr)
        stmt = stmt.limit(max(50, batch_size))

        result = await self.session.exec(stmt)
        rows = result.all()

        candidates: List[NearbyProviderResult] = []
        for loc, profile, dist_km, acceptance_rate, avg_rating, credibility in rows:
            candidates.append(
                NearbyProviderResult(
                    provider_id=loc.user_id,
                    distance_km=round(float(dist_km), 2) if dist_km is not None else 0.0,
                    latitude=loc.latitude,
                    longitude=loc.longitude,
                    last_heartbeat_at=loc.updated_at.isoformat() if loc.updated_at else None,
                    is_online=profile.is_online if profile.is_online is not None else True,
                    acceptance_rate_30d=acceptance_rate if acceptance_rate is not None else 100.0,
                    average_ratings=avg_rating if avg_rating is not None else 0.0,
                    credibility_score=credibility if credibility is not None else 0.0,
                )
            )

        _debug_log(
            f"fetch single-query combined candidate search returned {len(candidates)} raw candidate(s)"
        )
        return candidates


class CandidateScorer:
    """Scores candidate providers by acceptance rate, rating, credibility, and distance, returning top candidates."""

    def score(
        self,
        candidates: List[NearbyProviderResult],
        batch_size: int,
        current_radius: float,
    ) -> List[ScoredCandidate]:
        _debug_log(
            f"CandidateScorer.score ranking {len(candidates)} candidate(s)",
            f"batch_size={batch_size}, radius_km={current_radius}",
        )
        scored: List[ScoredCandidate] = []
        for c in candidates:
            if not c.provider_id:
                continue
            dist_km = c.distance_km if c.distance_km is not None else 10.0
            acceptance_rate = (
                c.acceptance_rate_30d
                if c.acceptance_rate_30d is not None
                else 100.0
            )
            avg_rating = c.average_ratings if c.average_ratings is not None else 0.0
            credibility = (
                c.credibility_score if c.credibility_score is not None else 0.0
            )

            normalized_dist = (
                (dist_km / current_radius) * 100.0 if current_radius > 0 else 0.0
            )
            score_val = (
                (0.30 * acceptance_rate)
                + (0.25 * avg_rating * 20.0)
                + (0.25 * credibility)
                - (0.20 * normalized_dist)
            )
            scored.append(
                ScoredCandidate(
                    user_id=c.provider_id,
                    distance_km=dist_km,
                    score=round(score_val, 2),
                )
            )

        scored.sort(key=lambda item: item.score, reverse=True)
        _debug_log(
            f"CandidateScorer.score ranked {len(scored)} candidate(s)",
            scored,
        )
        return scored


class CandidatePinger:
    """Handles creating attempt records, updating duty status, sending notifications, and stale attempt recovery."""

    def __init__(
        self,
        session: AsyncSession,
        notification_service: NotificationService,
        ping_duration: int = DispatchPolicy.PING_DURATION_SECONDS,
    ):
        self.session = session
        self.notification_service = notification_service
        self.ping_duration = ping_duration

    async def recover_stale_attempts(self, task: Task) -> None:
        """Local Recovery: clean up stale PENDING attempts using atomic UPDATE ... RETURNING."""
        now = lagos_now()
        stmt_stale = (
            update(TaskDispatchAttempt)
            .where(
                col(TaskDispatchAttempt.task_id) == task.id,
                col(TaskDispatchAttempt.status) == DispatchAttemptStatus.PENDING,  # type: ignore
                col(TaskDispatchAttempt.expires_at) <= now,
            )
            .values(status=DispatchAttemptStatus.TIMEOUT, responded_at=now)
            .returning(col(TaskDispatchAttempt.provider_id))
        )
        res_stale = await self.session.exec(stmt_stale)
        raw_stale = res_stale.scalars().all()
        stale_provider_ids = list(raw_stale)

        if stale_provider_ids:
            await self.session.exec(
                update(ProviderProfile)
                .where(
                    col(ProviderProfile.user_id).in_(stale_provider_ids),
                    col(ProviderProfile.duty_status) == DutyStatus.ON_DISPATCH,  # type: ignore
                )
                .values(duty_status=DutyStatus.ONLINE_AVAILABLE)
            )
            await self.session.commit()
            _debug_log(
                f"CandidatePinger.recover_stale_attempts recovered {len(stale_provider_ids)} stale attempt(s) for task {task.id}",
                stale_provider_ids,
            )
            logger.info(
                f"CandidatePinger: Recovered {len(stale_provider_ids)} stale attempts for task {task.id}"
            )
        else:
            _debug_log(f"CandidatePinger.recover_stale_attempts no stale attempts found for task {task.id}")

    async def ping_candidate(
        self,
        candidate: ScoredCandidate,
        task: Task,
        dispatch_session_id: str,
        sequence_order: int,
        ping_duration: Optional[int] = None,
    ) -> Optional[TaskDispatchAttempt]:
        """Creates dispatch attempt, updates provider duty status to ON_DISPATCH, and returns attempt."""
        _debug_log(
            f"CandidatePinger.ping_candidate pinging single candidate provider_id={candidate.user_id}",
            f"score={candidate.score}, task_id={task.id}",
        )
        effective_ping_duration = (
            ping_duration if ping_duration is not None else self.ping_duration
        )
        now = lagos_now()
        offered_payout = task.provider_payout or 0.0

        stmt_update = (
            update(ProviderProfile)
            .where(
                col(ProviderProfile.user_id) == candidate.user_id,
                col(ProviderProfile.duty_status) == DutyStatus.ONLINE_AVAILABLE,  # type: ignore
            )
            .values(duty_status=DutyStatus.ON_DISPATCH)
        )
        res_update = await self.session.exec(stmt_update)
        if res_update.rowcount == 0:
            _debug_log(
                f"CandidatePinger.ping_candidate candidate {candidate.user_id} lost race (duty status modified)"
            )
            logger.debug(
                f"CandidatePinger: Candidate {candidate.user_id} is no longer ONLINE_AVAILABLE (lost race). Skipping."
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
        self.session.add(attempt)
        _debug_log(
            f"CandidatePinger.ping_candidate created attempt for provider_id={candidate.user_id}",
            f"expires_at={attempt.expires_at}",
        )
        return attempt

    async def send_batch_notification(
        self,
        user_ids: List[str],
        task: Task,
        dispatch_session_id: str,
        offered_payout: float,
        expires_at: Optional[str] = None,
    ) -> None:
        """Sends a single notification to all candidate providers in the batch at once."""
        _debug_log(
            f"CandidatePinger.send_batch_notification notifying {len(user_ids)} provider(s) for task {task.id}",
            f"user_ids={user_ids}, payout={offered_payout}",
        )
        payout_fmt = to_naira(offered_payout) if offered_payout > 0 else "offered price"
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

    async def ping_batch(
        self,
        batch: List[ScoredCandidate],
        task: Task,
        dispatch_session_id: str,
        seq_start: int,
    ) -> List[TaskDispatchAttempt]:
        """Dispatches ping attempts to candidate batch using atomic UPDATE RETURNING and bulk INSERT."""
        _debug_log(
            f"CandidatePinger.ping_batch starting ping for {len(batch)} candidate(s), seq_start={seq_start}"
        )
        if not batch:
            return []

        candidate_ids = [c.user_id for c in batch]
        stmt_lock = (
            update(ProviderProfile)
            .where(
                col(ProviderProfile.user_id).in_(candidate_ids),
                col(ProviderProfile.duty_status) == DutyStatus.ONLINE_AVAILABLE,  # type: ignore
            )
            .values(duty_status=DutyStatus.ON_DISPATCH)
            .returning(col(ProviderProfile.user_id))
        )
        res_lock = await self.session.exec(stmt_lock)
        winning_provider_ids = set(res_lock.scalars().all())
        _debug_log(
            f"CandidatePinger.ping_batch duty status lock acquired for {len(winning_provider_ids)} provider(s)",
            winning_provider_ids,
        )

        if not winning_provider_ids:
            _debug_log("CandidatePinger.ping_batch lost race for all candidates in batch")
            return []

        now = lagos_now()
        offered_payout = task.provider_payout or 0.0
        effective_expires_at = now + timedelta(seconds=self.ping_duration)

        attempts: List[TaskDispatchAttempt] = []
        dispatched_user_ids: List[str] = []

        for candidate in batch:
            if candidate.user_id not in winning_provider_ids:
                logger.debug(
                    f"CandidatePinger: Candidate {candidate.user_id} lost race (duty status modified). Skipping."
                )
                continue

            attempt = TaskDispatchAttempt(
                dispatch_session_id=dispatch_session_id,
                task_id=task.id,
                provider_id=candidate.user_id,
                sequence_order=seq_start + len(attempts),
                match_score=candidate.score,
                offered_payout=offered_payout,
                pinged_at=now,
                expires_at=effective_expires_at,
                status=DispatchAttemptStatus.PENDING,
            )
            attempts.append(attempt)
            dispatched_user_ids.append(candidate.user_id)

        if not attempts:
            _debug_log("CandidatePinger.ping_batch no attempt objects created")
            return []

        self.session.add_all(attempts)

        last_expires_at = effective_expires_at.isoformat()

        try:
            await self.send_batch_notification(
                user_ids=dispatched_user_ids,
                task=task,
                dispatch_session_id=dispatch_session_id,
                offered_payout=offered_payout,
                expires_at=last_expires_at,
            )
        except Exception as e:
            _debug_log(
                f"CandidatePinger.ping_batch notification ERROR: {e}"
            )
            logger.error(
                f"CandidatePinger: Failed to send batch ping notification for task {task.id}: {e}"
            )

        _debug_log(
            f"CandidatePinger.ping_batch created and saved {len(attempts)} attempt(s)",
            dispatched_user_ids,
        )
        return attempts


class MatchingEngine:
    """Ephemeral matching engine that executes candidate discovery, candidate ranking,
    and provider attempt creation for a task dispatch session step.

    Responsibility: MATCHING ONLY.
    Does not schedule Celery retries, enforce retry limits, or handle task cancellations directly.
    """

    def __init__(
        self,
        session_id: str,
        session: Optional[AsyncSession] = None,
        ping_duration: int = 180,
        exclude_previous_sessions: bool = True,
        excluded_provider_ids: Optional[List[str]] = None,
        geo_service: Optional[GeoService] = None,
        notification_service: Optional[NotificationService] = None,
        system_logger: Optional[LoggerService] = None,
        fetcher: Optional[CandidateFetcher] = None,
        scorer: Optional[CandidateScorer] = None,
        pinger: Optional[CandidatePinger] = None,
        db_session: Optional[AsyncSession] = None,
    ):
        self.session_id = session_id
        effective_session = session if session is not None else db_session
        if effective_session is None:
            raise ValueError(
                "AsyncSession must be provided to MatchingEngine constructor."
            )
        self.session = effective_session
        self.db_session = effective_session
        self.ping_duration = ping_duration
        self.exclude_previous_sessions = exclude_previous_sessions
        self.excluded_provider_ids = (
            list(excluded_provider_ids) if excluded_provider_ids else []
        )

        if geo_service is None:
            geo_service = GeoService(session=self.session)
        self.geo_service = geo_service

        self.notification_service = (
            notification_service or get_notification_service_manual(self.session)
        )
        self.system_logger = system_logger or get_logger_service_manual(self.session)

        self.fetcher = fetcher or CandidateFetcher(
            session=self.session,
            geo_service=self.geo_service,
            exclude_previous_sessions=self.exclude_previous_sessions,
            excluded_provider_ids=self.excluded_provider_ids,
        )
        self.scorer = scorer or CandidateScorer()
        self.pinger = pinger or CandidatePinger(
            session=self.session,
            notification_service=self.notification_service,
            ping_duration=self.ping_duration,
        )

    # Delegated helper methods for backwards compatibility with tests / callers
    async def _get_excluded_provider_ids(
        self,

        excluded_provider_ids: Optional[List[str]] = None,

    ) -> List[str]:
        return await self.fetcher.get_excluded_ids(
            # task_id=task_id,
            excluded_provider_ids=excluded_provider_ids,
            # dispatch_session=dispatch_session,
        )

    async def _validate_task_for_matching(self, task: Task) -> Optional[TaskLocation]:
        return await self.fetcher.validate_task(task)

    async def _fetch_and_filter_candidates(
        self,
        task: Task,
        excluded_provider_ids: Optional[List[str]] = None,
        dispatch_session: Optional[DispatchSession] = None,
    ) -> List[ScoredCandidate]:
        candidates = await self.fetcher.fetch(
            task=task,
            excluded_provider_ids=excluded_provider_ids,
            dispatch_session=dispatch_session,
        )
        if not candidates:
            return []

        current_radius: float = (
            dispatch_session.search_radius_km
            if dispatch_session and dispatch_session.search_radius_km is not None
            else 10.0
        )
        batch_size = max(
            1,
            (
                dispatch_session.batch_size
                if (dispatch_session and dispatch_session.batch_size)
                else 5
            ),
        )
        return self.scorer.score(
            candidates=candidates,
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
        await self.pinger.send_batch_notification(
            user_ids=user_ids,
            task=task,
            dispatch_session_id=dispatch_session_id,
            offered_payout=offered_payout,
            expires_at=expires_at,
        )

    async def _dispatch_to_candidate(
        self,
        candidate: ScoredCandidate,
        task: Task,
        dispatch_session_id: str,
        sequence_order: int,
        ping_duration: Optional[int] = None,
    ) -> Optional[TaskDispatchAttempt]:
        return await self.pinger.ping_candidate(
            candidate=candidate,
            task=task,
            dispatch_session_id=dispatch_session_id,
            sequence_order=sequence_order,
            ping_duration=ping_duration,
        )

    async def _recover_stale_attempts(self, task: Task) -> None:
        await self.pinger.recover_stale_attempts(task)

    async def run(self) -> MatchingResult:
        """Executes a single step of candidate discovery, ranking, and ping attempt creation for a dispatch session.

        Returns:
            MatchingResult: Result containing matched status, candidate count, attempts created, and optional reason.
        """
        _debug_log(f"MatchingEngine.run STARTING for session_id={self.session_id}")
        logger.debug(f"MatchingEngine.run starting for session {self.session_id}")

        dispatch_session = await self.session.get(DispatchSession, self.session_id)
        if not dispatch_session:
            _debug_log(f"MatchingEngine.run SESSION_NOT_FOUND for session_id={self.session_id}")
            return MatchingResult(
                matched=False,
                candidates_count=0,
                attempts_created=0,
                reason="SESSION_NOT_FOUND",
            )

        _debug_log(
            f"MatchingEngine.run loaded dispatch_session",
            f"status={dispatch_session.status}, lock_version={dispatch_session.lock_version}, task_id={dispatch_session.task_id}",
        )

        if dispatch_session.status != DispatchSessionStatus.RUNNING:
            reason_str = f"SESSION_NOT_ACTIVE ({dispatch_session.status.value if hasattr(dispatch_session.status, 'value') else dispatch_session.status})"
            _debug_log(f"MatchingEngine.run ABORTED: {reason_str}")
            return MatchingResult(
                matched=False,
                candidates_count=0,
                attempts_created=0,
                reason=reason_str,
            )

        lock_version = dispatch_session.lock_version
        stmt_opt = (
            update(DispatchSession)
            .where(
                col(DispatchSession.id) == self.session_id,  # type: ignore
                col(DispatchSession.lock_version) == lock_version,  # type: ignore
            )
            .values(
                lock_version=lock_version + 1,
                updated_at=lagos_now(),
            )
        )
        res_opt = await self.session.exec(stmt_opt)
        if res_opt.rowcount == 0:
            _debug_log(f"MatchingEngine.run LOCKING_CONFLICT on session {self.session_id}")
            return MatchingResult(
                matched=False,
                candidates_count=0,
                attempts_created=0,
                reason="LOCKING_CONFLICT",
            )

        dispatch_session.lock_version = lock_version + 1
        _debug_log(f"MatchingEngine.run lock_version updated to {dispatch_session.lock_version}")

        task = await self.session.get(Task, dispatch_session.task_id)
        if not task:
            _debug_log(f"MatchingEngine.run TASK_NOT_FOUND for task_id={dispatch_session.task_id}")
            return MatchingResult(
                matched=False,
                candidates_count=0,
                attempts_created=0,
                reason="TASK_NOT_FOUND",
            )

        _debug_log(
            f"MatchingEngine.run loaded task {task.id}",
            f"title='{task.title}', service_id={task.service_id}, status={task.status}",
        )

        combined_excluded: Set[str] = set(self.excluded_provider_ids)
        if dispatch_session.excluded_provider_ids:
            combined_excluded.update(dispatch_session.excluded_provider_ids)
        excluded_ids_list = list(combined_excluded)

        await self.pinger.recover_stale_attempts(task)

        candidates = await self._fetch_and_filter_candidates(
            task,
            excluded_provider_ids=excluded_ids_list,
            dispatch_session=dispatch_session,
        )

        if not candidates:
            _debug_log(f"MatchingEngine.run NO_CANDIDATES found for session {self.session_id}")
            return MatchingResult(
                matched=False,
                candidates_count=0,
                attempts_created=0,
                reason="NO_CANDIDATES",
            )

        batch_size = max(
            1,
            (
                dispatch_session.batch_size
                if (dispatch_session and dispatch_session.batch_size)
                else 5
            ),
        )

        batches = [
            candidates[i : i + batch_size]
            for i in range(0, len(candidates), batch_size)
        ]

        _debug_log(
            f"MatchingEngine.run found {len(candidates)} candidate(s) split into {len(batches)} batch(es)"
        )

        total_attempts_created = 0
        matched_any = False
        seq_start = 1

        for batch_index, batch in enumerate(batches):
            task_current = await self.session.get(Task, task.id)
            session_current = await self.session.get(DispatchSession, self.session_id)

            if (
                not task_current
                or task_current.status != TaskStatus.SEARCHING
                or not session_current
                or session_current.status != DispatchSessionStatus.RUNNING
            ):
                _debug_log(
                    f"MatchingEngine.run task or session no longer SEARCHING/RUNNING before batch {batch_index + 1}. Halting."
                )
                if task_current and task_current.status == TaskStatus.ASSIGNED:
                    matched_any = True
                break

            attempts = await self.pinger.ping_batch(
                batch=batch,
                task=task_current,
                dispatch_session_id=dispatch_session.id,
                seq_start=seq_start,
            )

            if not attempts:
                _debug_log(f"MatchingEngine.run ALL_RACE_LOST for batch {batch_index + 1}")
                continue

            seq_start += len(attempts)
            total_attempts_created += len(attempts)

            if batch_index < len(batches) - 1:
                _debug_log(
                    f"MatchingEngine.run batch {batch_index + 1} pinged ({len(attempts)} attempt(s)). Waiting {self.ping_duration}s for response before next batch."
                )
                sleep_elapsed = 0.0
                check_interval = 2.0
                while sleep_elapsed < self.ping_duration:
                    await asyncio.sleep(check_interval)
                    sleep_elapsed += check_interval

                    task_check = await self.session.get(Task, task.id)
                    session_check = await self.session.get(DispatchSession, self.session_id)
                    if (
                        not task_check
                        or task_check.status != TaskStatus.SEARCHING
                        or not session_check
                        or session_check.status != DispatchSessionStatus.RUNNING
                    ):
                        if task_check and task_check.status == TaskStatus.ASSIGNED:
                            matched_any = True
                        break

                if matched_any:
                    break

                await self.pinger.recover_stale_attempts(task)

                task_post = await self.session.get(Task, task.id)
                session_post = await self.session.get(DispatchSession, self.session_id)
                if (
                    not task_post
                    or task_post.status != TaskStatus.SEARCHING
                    or not session_post
                    or session_post.status != DispatchSessionStatus.RUNNING
                ):
                    if task_post and task_post.status == TaskStatus.ASSIGNED:
                        matched_any = True
                    break

        final_task = await self.session.get(Task, task.id)
        if final_task and final_task.status == TaskStatus.ASSIGNED:
            matched_any = True

        if matched_any:
            res_final = MatchingResult(
                matched=True,
                candidates_count=len(candidates),
                attempts_created=total_attempts_created,
            )
            _debug_log(f"MatchingEngine.run COMPLETED MATCHED for session {self.session_id}", res_final)
            return res_final
        elif total_attempts_created > 0:
            res_final = MatchingResult(
                matched=False,
                candidates_count=len(candidates),
                attempts_created=total_attempts_created,
                reason="ALL_DECLINED_OR_TIMED_OUT",
            )
            _debug_log(
                f"MatchingEngine.run COMPLETED UNMATCHED (ALL_DECLINED_OR_TIMED_OUT) for session {self.session_id}",
                res_final,
            )
            return res_final
        else:
            return MatchingResult(
                matched=False,
                candidates_count=len(candidates),
                attempts_created=0,
                reason="ALL_RACE_LOST",
            )
