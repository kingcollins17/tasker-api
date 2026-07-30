import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.models.tasks import (
    DispatchSession,
    DispatchSessionStatus,
    Task,
    TaskDispatchAttempt,
    TaskStatus,
)
from app.core.models.users import DutyStatus, ProviderProfile, User
from app.core.services.matching_engine import MatchingEngine
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
    user = User(id="provider-1", email="prov@example.com", average_ratings=4.8, credibility_score=95.0)
    profile = ProviderProfile(id="prof-1", user_id="provider-1", acceptance_rate_30d=95.0, duty_status=DutyStatus.ONLINE_AVAILABLE)

    mock_db_session.get.side_effect = lambda model, id_val: (
        searching_session if model == DispatchSession else task
    )

    mock_exec_res = MagicMock()
    mock_exec_res.rowcount = 1
    mock_exec_res.one_or_none.side_effect = [None, profile]
    mock_exec_res.all.return_value = []
    mock_db_session.exec.return_value = mock_exec_res

    engine = MatchingEngine(session_id=session_id, db_session=mock_db_session)
    engine._fetch_and_filter_candidates = AsyncMock(return_value=[(user, profile, 2.5)])
    engine.notification_service.notify = AsyncMock()

    from unittest.mock import patch
    with patch("app.features.tasks.celery.dispatch.execute_matching_engine_task.apply_async") as mock_apply_async:
        result = await engine.run()

        assert result is True
        assert mock_db_session.add.called
        assert mock_apply_async.called
        assert mock_apply_async.call_args[1]["countdown"] == 180


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
    user = User(id="provider-2", email="prov2@example.com", average_ratings=5.0, credibility_score=90.0)
    profile = ProviderProfile(id="prof-2", user_id="provider-2", acceptance_rate_30d=100.0, duty_status=DutyStatus.ONLINE_AVAILABLE)

    mock_db_session.get.side_effect = lambda model, id_val: (
        searching_session if model == DispatchSession else task
    )

    mock_exec_res = MagicMock()
    mock_exec_res.rowcount = 1
    mock_exec_res.one_or_none.side_effect = [None, profile]
    mock_exec_res.all.return_value = []
    mock_db_session.exec.return_value = mock_exec_res

    custom_ping = 300
    engine = MatchingEngine(session_id=session_id, db_session=mock_db_session, ping_duration=custom_ping)
    engine._fetch_and_filter_candidates = AsyncMock(return_value=[(user, profile, 1.0)])
    engine.notification_service.notify = AsyncMock()

    from unittest.mock import patch
    with patch("app.features.tasks.celery.dispatch.execute_matching_engine_task.apply_async") as mock_apply_async:
        result = await engine.run()

        assert result is True
        assert mock_apply_async.call_args[1]["countdown"] == custom_ping


