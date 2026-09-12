from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import Row, func, update
from sqlalchemy.orm import selectinload
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import IS_LOCAL
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
    """Handles candidate location validation, exclusion filtering, PostGIS discovery, and eligibility querying."""

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
        task_id: str,
        excluded_provider_ids: Optional[List[str]] = None,
        dispatch_session: Optional[DispatchSession] = None,
    ) -> List[str]:
        """Combines excluded provider IDs from constructor, method parameters, and DB dispatch attempts."""
        all_excluded: Set[str] = set(self.excluded_provider_ids)
        if excluded_provider_ids:
            all_excluded.update(excluded_provider_ids)

        stmt_attempts = select(TaskDispatchAttempt.provider_id).where(
            col(TaskDispatchAttempt.task_id) == task_id
        )
        if not self.exclude_previous_sessions and dispatch_session:
            stmt_attempts = stmt_attempts.where(
                col(TaskDispatchAttempt.dispatch_session_id) == dispatch_session.id
            )
        res_attempts = await self.session.exec(stmt_attempts)
        raw_attempts = res_attempts.all()
        attempted_ids: Set[str] = {
            (row[0] if isinstance(row, (tuple, Row)) else row)
            for row in raw_attempts
            if (row[0] if isinstance(row, (tuple, Row)) else row) is not None
        }

        all_excluded.update(attempted_ids)
        return list(all_excluded)

    async def validate_task(self, task: Task) -> Optional[TaskLocation]:
        """Validates if task is fit for matching and returns TaskLocation."""
        if not task.service_id:
            return None

        stmt_loc = (
            select(TaskLocation).where(col(TaskLocation.task_id) == task.id).limit(1)
        )
        res_loc = await self.session.exec(stmt_loc)

        task_loc: Optional[TaskLocation] = res_loc.one_or_none()
        if not task_loc or task_loc.latitude is None or task_loc.longitude is None:
            return None

        return task_loc

    async def get_eligible(
        self,
        provider_ids: List[str],
        service_id: str,
    ) -> List[Tuple[User, ProviderProfile]]:
        """Queries DB for active, verified, online providers matching service_id."""
        if not provider_ids:
            return []

        stmt_eligibility = (
            select(User, ProviderProfile)
            .join(ProviderProfile, col(ProviderProfile.user_id) == col(User.id))  # type: ignore
            .join(
                ProviderServiceLink,
                col(ProviderServiceLink.provider_id) == col(ProviderProfile.user_id),  # type: ignore
            )
            .where(
                col(User.id).in_(provider_ids),  # type: ignore
                col(User.is_active) == True,  # noqa: E712
                col(ProviderProfile.status) == KYCStatus.VERIFIED,
                col(ProviderServiceLink.service_id) == service_id,
                col(ProviderProfile.is_online) == True,  # noqa: E712
                col(ProviderProfile.duty_status) == DutyStatus.ONLINE_AVAILABLE,
            )
        )

        res_eligibility = await self.session.exec(stmt_eligibility)
        # pyrefly: ignore [missing-attribute]
        return res_eligibility.unique().all()

    async def fetch(
        self,
        task: Task,
        excluded_provider_ids: Optional[List[str]] = None,
        dispatch_session: Optional[DispatchSession] = None,
    ) -> EligibleCandidates:
        """Discovers nearby providers and excludes already pinged/excluded candidates at DB level."""
        task_loc = await self.validate_task(task)
        if not task_loc:
            return EligibleCandidates(rows=[], nearby_map={})

        all_excluded_list = await self.get_excluded_ids(
            task_id=task.id,
            excluded_provider_ids=excluded_provider_ids,
            dispatch_session=dispatch_session,
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

        limit_pool = 50
        local_excluded = set(all_excluded_list)
        all_eligible_rows: List[Tuple[User, ProviderProfile]] = []
        nearby_map: Dict[str, float] = {}

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
                break

            for r in nearby_results:
                if r.provider_id and r.distance_km is not None:
                    nearby_map[r.provider_id] = r.distance_km

            provider_ids = [r.provider_id for r in nearby_results if r.provider_id]
            local_excluded.update(provider_ids)
            if provider_ids:
                assert task.service_id, "Task service id must not be null"
                rows = await self.get_eligible(
                    provider_ids=provider_ids,
                    service_id=task.service_id,
                )
                all_eligible_rows.extend(rows)

            if len(all_eligible_rows) >= batch_size or len(nearby_results) < limit_pool:
                break

        return EligibleCandidates(rows=all_eligible_rows, nearby_map=nearby_map)


class CandidateScorer:
    """Scores candidate providers by acceptance rate, rating, credibility, and distance, returning top candidates."""

    def score(
        self,
        rows: List[Tuple[User, ProviderProfile]],
        nearby_map: Dict[str, float],
        batch_size: int,
        current_radius: float,
    ) -> List[ScoredCandidate]:
        scored: List[ScoredCandidate] = []
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

            normalized_dist = (
                (dist_km / current_radius) * 100 if current_radius > 0 else 0
            )
            score_val = (
                (0.30 * acceptance_rate)
                + (0.25 * avg_rating * 20.0)
                + (0.25 * credibility)
                - (0.20 * normalized_dist)
            )
            scored.append(
                ScoredCandidate(
                    user_id=user.id,
                    distance_km=dist_km,
                    score=round(score_val, 2),
                )
            )

        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:batch_size]


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
        """Local Recovery: clean up stale PENDING attempts from previous iterations."""
        stmt_stale = select(
            TaskDispatchAttempt.id, TaskDispatchAttempt.provider_id
        ).where(
            col(TaskDispatchAttempt.task_id) == task.id,
            col(TaskDispatchAttempt.status) == DispatchAttemptStatus.PENDING,  # type: ignore
            col(TaskDispatchAttempt.expires_at) <= lagos_now(),
        )
        res_stale = await self.session.exec(stmt_stale)
        stale_records = res_stale.all()
        if stale_records:
            stale_attempt_ids = [r[0] for r in stale_records]
            stale_provider_ids = [r[1] for r in stale_records]

            await self.session.exec(
                update(TaskDispatchAttempt)
                .where(col(TaskDispatchAttempt.id).in_(stale_attempt_ids))
                .values(status=DispatchAttemptStatus.TIMEOUT, responded_at=lagos_now())
            )
            await self.session.exec(
                update(ProviderProfile)
                .where(
                    col(ProviderProfile.user_id).in_(stale_provider_ids),
                    col(ProviderProfile.duty_status) == DutyStatus.ON_DISPATCH,  # type: ignore
                )
                .values(duty_status=DutyStatus.ONLINE_AVAILABLE)
            )
            await self.session.commit()
            logger.info(
                f"CandidatePinger: Recovered {len(stale_attempt_ids)} stale attempts for task {task.id}"
            )

    async def ping_candidate(
        self,
        candidate: ScoredCandidate,
        task: Task,
        dispatch_session_id: str,
        sequence_order: int,
        ping_duration: Optional[int] = None,
    ) -> Optional[TaskDispatchAttempt]:
        """Creates dispatch attempt, updates provider duty status to ON_DISPATCH, and returns attempt."""
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
        """Dispatches ping attempts to a batch of candidates and sends ping notifications."""
        attempts: List[TaskDispatchAttempt] = []
        dispatched_user_ids: List[str] = []

        for candidate in batch:
            attempt = await self.ping_candidate(
                candidate=candidate,
                task=task,
                dispatch_session_id=dispatch_session_id,
                sequence_order=seq_start + len(attempts),
                ping_duration=self.ping_duration,
            )
            if not attempt:
                continue

            attempts.append(attempt)
            dispatched_user_ids.append(candidate.user_id)

        if not attempts:
            return []

        offered_payout = task.provider_payout or 0.0
        last_expires_at = (
            attempts[-1].expires_at.isoformat()
            if attempts and attempts[-1].expires_at
            else None
        )

        try:
            await self.send_batch_notification(
                user_ids=dispatched_user_ids,
                task=task,
                dispatch_session_id=dispatch_session_id,
                offered_payout=offered_payout,
                expires_at=last_expires_at,
            )
        except Exception as e:
            logger.error(
                f"CandidatePinger: Failed to send batch ping notification for task {task.id}: {e}"
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
        task_id: str,
        excluded_provider_ids: Optional[List[str]] = None,
        dispatch_session: Optional[DispatchSession] = None,
    ) -> List[str]:
        return await self.fetcher.get_excluded_ids(
            task_id=task_id,
            excluded_provider_ids=excluded_provider_ids,
            dispatch_session=dispatch_session,
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
        if not candidates.rows:
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
            rows=candidates.rows,
            nearby_map=candidates.nearby_map,
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
        logger.debug(f"MatchingEngine.run starting for session {self.session_id}")

        dispatch_session = await self.session.get(DispatchSession, self.session_id)
        if not dispatch_session:
            return MatchingResult(
                matched=False,
                candidates_count=0,
                attempts_created=0,
                reason="SESSION_NOT_FOUND",
            )

        if dispatch_session.status != DispatchSessionStatus.RUNNING:
            return MatchingResult(
                matched=False,
                candidates_count=0,
                attempts_created=0,
                reason=f"SESSION_NOT_ACTIVE ({dispatch_session.status.value if hasattr(dispatch_session.status, 'value') else dispatch_session.status})",
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
            return MatchingResult(
                matched=False,
                candidates_count=0,
                attempts_created=0,
                reason="LOCKING_CONFLICT",
            )

        await self.session.refresh(dispatch_session)

        task = await self.session.get(Task, dispatch_session.task_id)
        if not task:
            return MatchingResult(
                matched=False,
                candidates_count=0,
                attempts_created=0,
                reason="TASK_NOT_FOUND",
            )

        combined_excluded: Set[str] = set(self.excluded_provider_ids)
        if dispatch_session.excluded_provider_ids:
            combined_excluded.update(dispatch_session.excluded_provider_ids)
        excluded_ids_list = list(combined_excluded)

        await self.pinger.recover_stale_attempts(task)

        batch = await self._fetch_and_filter_candidates(
            task,
            excluded_provider_ids=excluded_ids_list,
            dispatch_session=dispatch_session,
        )

        if not batch:
            return MatchingResult(
                matched=False,
                candidates_count=0,
                attempts_created=0,
                reason="NO_CANDIDATES",
            )

        stmt_attempts_count = select(
            func.max(TaskDispatchAttempt.sequence_order)
        ).where(col(TaskDispatchAttempt.dispatch_session_id) == dispatch_session.id)
        res_count = await self.session.exec(stmt_attempts_count)
        raw_count = res_count.one_or_none()
        max_seq = raw_count[0] if isinstance(raw_count, (tuple, Row)) else raw_count
        seq_start = (max_seq or 0) + 1

        attempts = await self.pinger.ping_batch(
            batch=batch,
            task=task,
            dispatch_session_id=dispatch_session.id,
            seq_start=seq_start,
        )

        if not attempts:
            return MatchingResult(
                matched=False,
                candidates_count=len(batch),
                attempts_created=0,
                reason="ALL_RACE_LOST",
            )

        return MatchingResult(
            matched=True,
            candidates_count=len(batch),
            attempts_created=len(attempts),
        )
