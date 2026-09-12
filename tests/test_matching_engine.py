import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.models.tasks import (
    DispatchAttemptStatus,
    DispatchSession,
    DispatchSessionStatus,
    Task,
    TaskDispatchAttempt,
    TaskStatus,
)
from app.core.models.users import DutyStatus, ProviderProfile, User
from app.core.services.matching_engine import (
    CandidateFetcher,
    CandidatePinger,
    CandidateScorer,
    MatchingEngine,
    MatchingResult,
    ScoredCandidate,
    _ScoredCandidate,
)
from app.core.services.geo_service import NearbyProviderResult
from app.core.utils.datetime_helper import lagos_now


@pytest.fixture
def mock_db_session():
    session = MagicMock()
    session.get = AsyncMock()
    session.exec = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_matching_engine_skips_non_searching_session(mock_db_session):
    session_id = "session-123"
    non_searching_session = DispatchSession(
        id=session_id,
        task_id="task-123",
        status=DispatchSessionStatus.ASSIGNED,
    )
    mock_db_session.get.return_value = non_searching_session

    engine = MatchingEngine(session_id=session_id, session=mock_db_session)
    result = await engine.run()

    assert result.matched is False
    assert "SESSION_NOT_ACTIVE" in (result.reason or "")


@pytest.mark.asyncio
async def test_matching_engine_optimistic_locking_conflict(mock_db_session):
    session_id = "session-123"
    searching_session = DispatchSession(
        id=session_id,
        task_id="task-123",
        status=DispatchSessionStatus.RUNNING,
    )
    mock_db_session.get.return_value = searching_session

    # Simulate 0 rows updated by update statement (concurrency conflict)
    mock_exec_res = MagicMock()
    mock_exec_res.rowcount = 0
    mock_db_session.exec.return_value = mock_exec_res

    engine = MatchingEngine(session_id=session_id, session=mock_db_session)
    result = await engine.run()

    assert result.matched is False
    assert result.reason == "LOCKING_CONFLICT"


@pytest.mark.asyncio
async def test_matching_engine_returns_no_candidates(mock_db_session):
    session_id = "session-123"
    searching_session = DispatchSession(
        id=session_id,
        task_id="task-123",
        status=DispatchSessionStatus.RUNNING,
    )
    task = Task(id="task-123", title="Test Task", description="Desc", status=TaskStatus.SEARCHING)

    mock_db_session.get.side_effect = lambda model, id_val: (
        searching_session if model == DispatchSession else task
    )

    mock_exec_res = MagicMock()
    mock_exec_res.rowcount = 1
    mock_exec_res.one_or_none.return_value = None
    mock_exec_res.all.return_value = []
    mock_db_session.exec.return_value = mock_exec_res

    engine = MatchingEngine(session_id=session_id, session=mock_db_session)
    engine._fetch_and_filter_candidates = AsyncMock(return_value=[])

    result = await engine.run()

    assert result.matched is False
    assert result.reason == "NO_CANDIDATES"


@pytest.mark.asyncio
async def test_matching_engine_dispatches_candidate_batch(mock_db_session):
    session_id = "session-123"
    searching_session = DispatchSession(
        id=session_id,
        task_id="task-123",
        status=DispatchSessionStatus.RUNNING,
        batch_size=1,
    )
    task = Task(id="task-123", title="Cleaning Task", description="Need cleaning", status=TaskStatus.SEARCHING, provider_payout=15000.0)

    mock_db_session.get.side_effect = lambda model, id_val: (
        searching_session if model == DispatchSession else task
    )

    mock_exec_res = MagicMock()
    mock_exec_res.rowcount = 1
    mock_exec_res.all.return_value = []
    mock_db_session.exec.return_value = mock_exec_res

    engine = MatchingEngine(session_id=session_id, session=mock_db_session)
    candidate = _ScoredCandidate(user_id="provider-1", distance_km=1.0, score=2.5)
    engine._fetch_and_filter_candidates = AsyncMock(return_value=[candidate])
    engine.notification_service.notify = AsyncMock()

    result = await engine.run()

    assert result.matched is True
    assert result.attempts_created == 1


@pytest.mark.asyncio
async def test_matching_engine_excludes_provider_ids_from_session(mock_db_session):
    session_id = "session-789"
    searching_session = DispatchSession(
        id=session_id,
        task_id="task-789",
        status=DispatchSessionStatus.RUNNING,
        batch_size=2,
        excluded_provider_ids=["provider-1"],
    )
    task = Task(id="task-789", title="Task 789", description="Desc", service_id="srv-1", status=TaskStatus.SEARCHING)

    task_location = MagicMock()
    task_location.latitude = 6.5
    task_location.longitude = 3.4

    engine = MatchingEngine(session_id=session_id, session=mock_db_session)

    mock_attempts_res = MagicMock(all=lambda: [])
    mock_loc_res = MagicMock(one_or_none=lambda: task_location)

    mock_user2 = MagicMock()
    mock_user2.id = "provider-2"
    mock_user2.is_active = True
    mock_user2.average_ratings = 4.0
    mock_user2.credibility_score = 80.0

    mock_profile2 = MagicMock()
    mock_profile2.status = "VERIFIED"
    mock_profile2.is_online = True
    mock_profile2.duty_status = DutyStatus.ONLINE_AVAILABLE
    mock_profile2.acceptance_rate_30d = 90.0

    mock_eligibility_res = MagicMock()
    mock_eligibility_res.unique.return_value.all.return_value = [(mock_user2, mock_profile2)]

    def exec_side_effect(stmt):
        stmt_str = str(stmt)
        if "task_dispatch_attempts" in stmt_str:
            return mock_attempts_res
        elif "task_locations" in stmt_str:
            return mock_loc_res
        elif "provider_profiles" in stmt_str or "users" in stmt_str:
            return mock_eligibility_res
        return MagicMock(all=lambda: [], one_or_none=lambda: None)

    mock_db_session.exec.side_effect = exec_side_effect

    engine.geo_service.search_nearby_providers = AsyncMock(
        return_value=[
            NearbyProviderResult(provider_id="provider-1", distance_km=1.0, is_online=True),
            NearbyProviderResult(provider_id="provider-2", distance_km=1.0, is_online=True),
        ]
    )

    batch = await engine._fetch_and_filter_candidates(
        task=task,
        excluded_provider_ids=searching_session.excluded_provider_ids,
        dispatch_session=searching_session,
    )

    user_ids = [c.user_id for c in batch]
    assert "provider-1" not in user_ids
    assert "provider-2" in user_ids


@pytest.mark.asyncio
async def test_get_excluded_provider_ids_unpacks_row_tuples(mock_db_session):
    engine = MatchingEngine(session_id="session-tuple", session=mock_db_session)

    mock_res = MagicMock()
    mock_res.all.return_value = [("prov-1",), ("prov-2",), (None,)]
    mock_db_session.exec.return_value = mock_res

    excluded = await engine._get_excluded_provider_ids(task_id="task-123")
    assert set(excluded) == {"prov-1", "prov-2"}


def test_candidate_scorer_ranks_by_score():
    scorer = CandidateScorer()

    user1 = MagicMock(id="p1", average_ratings=4.8, credibility_score=95.0)
    profile1 = MagicMock(acceptance_rate_30d=98.0)

    user2 = MagicMock(id="p2", average_ratings=3.5, credibility_score=60.0)
    profile2 = MagicMock(acceptance_rate_30d=70.0)

    rows = [(user1, profile1), (user2, profile2)]
    nearby_map = {"p1": 2.0, "p2": 8.0}

    scored = scorer.score(rows=rows, nearby_map=nearby_map, batch_size=5, current_radius=10.0)
    assert len(scored) == 2
    assert scored[0].user_id == "p1"
    assert scored[1].user_id == "p2"
    assert scored[0].score > scored[1].score


@pytest.mark.asyncio
async def test_candidate_pinger_ping_candidate(mock_db_session):
    mock_exec_res = MagicMock()
    mock_exec_res.rowcount = 1
    mock_db_session.exec.return_value = mock_exec_res

    notification_service = MagicMock()

    pinger = CandidatePinger(
        session=mock_db_session,
        notification_service=notification_service,
        ping_duration=180,
    )

    candidate = ScoredCandidate(user_id="p1", distance_km=1.5, score=85.0)
    task = Task(id="t1", title="Test Task", provider_payout=5000.0)

    attempt = await pinger.ping_candidate(
        candidate=candidate,
        task=task,
        dispatch_session_id="s1",
        sequence_order=1,
    )

    assert attempt is not None
    assert attempt.provider_id == "p1"
    assert attempt.task_id == "t1"
    assert attempt.match_score == 85.0
    mock_db_session.add.assert_called_once_with(attempt)
