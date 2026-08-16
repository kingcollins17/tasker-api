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
from app.core.services.matching_engine import MatchingEngine, _ScoredCandidate
from app.core.services.provider_location import NearbyProviderResult
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
        current_batch=1,
    )
    mock_db_session.get.return_value = non_searching_session

    engine = MatchingEngine(session_id=session_id, db_session=mock_db_session)
    result = await engine.run()

    assert result is False


@pytest.mark.asyncio
async def test_matching_engine_optimistic_locking_conflict(mock_db_session):
    session_id = "session-123"
    searching_session = DispatchSession(
        id=session_id,
        task_id="task-123",
        status=DispatchSessionStatus.SEARCHING,
        current_batch=1,
    )
    mock_db_session.get.return_value = searching_session

    # Simulate 0 rows updated by update statement (concurrency conflict)
    mock_exec_res = MagicMock()
    mock_exec_res.rowcount = 0
    mock_db_session.exec.return_value = mock_exec_res

    engine = MatchingEngine(session_id=session_id, db_session=mock_db_session)
    result = await engine.run()

    assert result is False


@pytest.mark.asyncio
async def test_matching_engine_expires_session_when_candidates_exhausted(mock_db_session):
    session_id = "session-123"
    searching_session = DispatchSession(
        id=session_id,
        task_id="task-123",
        status=DispatchSessionStatus.SEARCHING,
        current_batch=1,
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

    engine = MatchingEngine(session_id=session_id, db_session=mock_db_session)
    engine._fetch_and_filter_candidates = AsyncMock(return_value=[])

    result = await engine.run()

    assert result is False
    assert searching_session.status == DispatchSessionStatus.EXPIRED
    assert task.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_matching_engine_dispatches_candidate_batch(mock_db_session):
    session_id = "session-123"
    searching_session = DispatchSession(
        id=session_id,
        task_id="task-123",
        status=DispatchSessionStatus.SEARCHING,
        batch_size=1,
        current_batch=1,
    )
    task = Task(id="task-123", title="Cleaning Task", description="Need cleaning", status=TaskStatus.SEARCHING, provider_payout=15000.0)

    mock_db_session.get.side_effect = lambda model, id_val: (
        searching_session if model == DispatchSession else task
    )

    mock_exec_res = MagicMock()
    mock_exec_res.rowcount = 1
    mock_exec_res.all.return_value = []
    mock_db_session.exec.return_value = mock_exec_res

    engine = MatchingEngine(session_id=session_id, db_session=mock_db_session)
    candidate = _ScoredCandidate(user_id="provider-1", distance_km=1.0, score=2.5)
    engine._fetch_and_filter_candidates = AsyncMock(return_value=[candidate])
    engine.notification_service.notify = AsyncMock()

    from unittest.mock import patch
    with patch("app.features.tasks.celery.dispatch.execute_matching_engine_task.apply_async") as mock_apply_async:
        result = await engine.run()

        assert result is True
        assert mock_db_session.add.called
        assert mock_apply_async.called
        assert mock_apply_async.call_args[1]["countdown"] == 300


@pytest.mark.asyncio
async def test_matching_engine_custom_ping_duration(mock_db_session):
    session_id = "session-456"
    searching_session = DispatchSession(
        id=session_id,
        task_id="task-456",
        status=DispatchSessionStatus.SEARCHING,
        batch_size=1,
        current_batch=1,
    )
    task = Task(id="task-456", title="Plumbing Task", description="Need plumbing", status=TaskStatus.SEARCHING, provider_payout=20000.0)

    mock_db_session.get.side_effect = lambda model, id_val: (
        searching_session if model == DispatchSession else task
    )

    mock_exec_res = MagicMock()
    mock_exec_res.rowcount = 1
    mock_exec_res.all.return_value = []
    mock_db_session.exec.return_value = mock_exec_res

    custom_ping = 300
    engine = MatchingEngine(session_id=session_id, db_session=mock_db_session, ping_duration=custom_ping)
    candidate = _ScoredCandidate(user_id="provider-2", distance_km=1.0, score=1.0)
    engine._fetch_and_filter_candidates = AsyncMock(return_value=[candidate])
    engine.notification_service.notify = AsyncMock()

    from unittest.mock import patch
    with patch("app.features.tasks.celery.dispatch.execute_matching_engine_task.apply_async") as mock_apply_async:
        result = await engine.run()

        assert result is True
        assert mock_apply_async.call_args[1]["countdown"] == custom_ping + 120


@pytest.mark.asyncio
async def test_matching_engine_excludes_provider_ids_from_session(mock_db_session):
    session_id = "session-789"
    searching_session = DispatchSession(
        id=session_id,
        task_id="task-789",
        status=DispatchSessionStatus.SEARCHING,
        batch_size=2,
        current_batch=1,
        excluded_provider_ids=["provider-1"],
    )

    engine = MatchingEngine(session_id=session_id, db_session=mock_db_session)
    engine.attempt_repo.get_all = AsyncMock(return_value=[])

    cand1 = _ScoredCandidate(user_id="provider-1", distance_km=1.0, score=5.0)
    cand2 = _ScoredCandidate(user_id="provider-2", distance_km=1.0, score=4.0)
    scored_candidates = [cand1, cand2]

    batch = await engine._get_next_batch(
        scored_candidates=scored_candidates,
        task_id="task-789",
        batch_size=2,
        dispatch_session_id=session_id,
        excluded_provider_ids=searching_session.excluded_provider_ids,
    )

    user_ids = [c.user_id for c in batch]
    assert "provider-1" not in user_ids
    assert "provider-2" in user_ids


@pytest.mark.asyncio
async def test_matching_engine_exclude_previous_sessions_toggle(mock_db_session):
    session_id = "session-new"
    prev_attempt = TaskDispatchAttempt(
        dispatch_session_id="session-old",
        task_id="task-100",
        provider_id="provider-old",
        status=DispatchAttemptStatus.DECLINED,
    )

    cand_old = _ScoredCandidate(user_id="provider-old", distance_km=1.0, score=5.0)
    cand_new = _ScoredCandidate(user_id="provider-new", distance_km=1.0, score=4.0)
    scored = [cand_old, cand_new]

    # Case A: exclude_previous_sessions=True (default) -> provider-old is excluded
    engine_exclude_prev = MatchingEngine(
        session_id=session_id,
        db_session=mock_db_session,
        exclude_previous_sessions=True,
    )
    engine_exclude_prev.attempt_repo.get_all = AsyncMock(return_value=[prev_attempt])

    batch_exclude = await engine_exclude_prev._get_next_batch(
        scored_candidates=scored,
        task_id="task-100",
        batch_size=5,
        dispatch_session_id=session_id,
    )
    user_ids_exclude = [c.user_id for c in batch_exclude]
    assert "provider-old" not in user_ids_exclude

    # Case B: exclude_previous_sessions=False -> query attempts for current session only
    engine_include_prev = MatchingEngine(
        session_id=session_id,
        db_session=mock_db_session,
        exclude_previous_sessions=False,
    )
    engine_include_prev.attempt_repo.get_all = AsyncMock(return_value=[])

    batch_include = await engine_include_prev._get_next_batch(
        scored_candidates=scored,
        task_id="task-100",
        batch_size=5,
        dispatch_session_id=session_id,
    )
    user_ids_include = [c.user_id for c in batch_include]
    assert "provider-old" in user_ids_include



