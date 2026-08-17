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
        excluded_provider_ids=["provider-1"],
    )
    task = Task(id="task-789", title="Task 789", description="Desc", service_id="srv-1", status=TaskStatus.SEARCHING)

    task_location = MagicMock()
    task_location.latitude = 6.5
    task_location.longitude = 3.4

    engine = MatchingEngine(session_id=session_id, db_session=mock_db_session)
    engine.attempt_repo.execute = AsyncMock(return_value=MagicMock(all=lambda: []))
    engine.task_location_repo.execute = AsyncMock(return_value=MagicMock(one_or_none=lambda: task_location))

    engine.geo_service.search_nearby_providers = AsyncMock(
        return_value=[
            NearbyProviderResult(provider_id="provider-1", distance_km=1.0, is_online=True),
            NearbyProviderResult(provider_id="provider-2", distance_km=1.0, is_online=True),
        ]
    )

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
    engine.provider_profile_repo.execute = AsyncMock(return_value=mock_eligibility_res)

    batch = await engine._fetch_and_filter_candidates(
        task=task,
        excluded_provider_ids=searching_session.excluded_provider_ids,
        dispatch_session=searching_session,
    )

    user_ids = [c.user_id for c in batch]
    assert "provider-1" not in user_ids
    assert "provider-2" in user_ids


@pytest.mark.asyncio
async def test_matching_engine_exclude_previous_sessions_toggle(mock_db_session):
    session_id = "session-new"
    task = Task(id="task-100", title="Task 100", description="Desc", service_id="srv-1", status=TaskStatus.SEARCHING)

    task_location = MagicMock()
    task_location.latitude = 6.5
    task_location.longitude = 3.4

    # Case A: exclude_previous_sessions=True (default) -> provider-old is excluded via DB attempt query
    engine_exclude_prev = MatchingEngine(
        session_id=session_id,
        db_session=mock_db_session,
        exclude_previous_sessions=True,
    )
    engine_exclude_prev.attempt_repo.execute = AsyncMock(return_value=MagicMock(all=lambda: ["provider-old"]))
    engine_exclude_prev.task_location_repo.execute = AsyncMock(return_value=MagicMock(one_or_none=lambda: task_location))

    engine_exclude_prev.geo_service.search_nearby_providers = AsyncMock(
        return_value=[
            NearbyProviderResult(provider_id="provider-old", distance_km=1.0, is_online=True),
            NearbyProviderResult(provider_id="provider-new", distance_km=1.0, is_online=True),
        ]
    )

    mock_user_new = MagicMock()
    mock_user_new.id = "provider-new"
    mock_user_new.is_active = True
    mock_user_new.average_ratings = 4.0
    mock_user_new.credibility_score = 80.0

    mock_profile_new = MagicMock()
    mock_profile_new.status = "VERIFIED"
    mock_profile_new.is_online = True
    mock_profile_new.duty_status = DutyStatus.ONLINE_AVAILABLE
    mock_profile_new.acceptance_rate_30d = 90.0

    mock_eligibility_res = MagicMock()
    mock_eligibility_res.unique.return_value.all.return_value = [(mock_user_new, mock_profile_new)]
    engine_exclude_prev.provider_profile_repo.execute = AsyncMock(return_value=mock_eligibility_res)

    batch_exclude = await engine_exclude_prev._fetch_and_filter_candidates(
        task=task,
    )
    user_ids_exclude = [c.user_id for c in batch_exclude]
    assert "provider-old" not in user_ids_exclude
    assert "provider-new" in user_ids_exclude


@pytest.mark.asyncio
async def test_dispatch_session_search_radius_defaults():
    session = DispatchSession(
        task_id="task-001",
        status=DispatchSessionStatus.SEARCHING,
    )
    assert session.search_radius_km == 10.0
    assert session.max_search_radius_km == 30.0
    assert session.auto_expand_radius is True


@pytest.mark.asyncio
async def test_matching_engine_auto_expands_search_radius(mock_db_session):
    session_id = "session-expand"
    searching_session = DispatchSession(
        id=session_id,
        task_id="task-expand",
        status=DispatchSessionStatus.SEARCHING,
        search_radius_km=10.0,
        max_search_radius_km=30.0,
        auto_expand_radius=True,
        search_offset=10,
    )
    task = Task(id="task-expand", title="Expand Task", description="Desc", service_id="srv-1", status=TaskStatus.SEARCHING)
    
    task_location = MagicMock()
    task_location.latitude = 6.5
    task_location.longitude = 3.4

    engine = MatchingEngine(session_id=session_id, db_session=mock_db_session)
    engine.attempt_repo.execute = AsyncMock(return_value=MagicMock(all=lambda: []))
    engine.task_location_repo.execute = AsyncMock(return_value=MagicMock(one_or_none=lambda: task_location))
    
    # Mock search_nearby_providers: 0 results at 10km, 1 result at 20km
    async def mock_search_nearby(latitude, longitude, radius_km, limit=5, service_id=None, offset=0):
        if radius_km < 20.0:
            return []
        return [NearbyProviderResult(provider_id="provider-far", distance_km=18.0, is_online=True)]

    engine.geo_service.search_nearby_providers = AsyncMock(side_effect=mock_search_nearby)

    mock_user = MagicMock()
    mock_user.id = "provider-far"
    mock_user.is_active = True
    mock_user.average_ratings = 4.8
    mock_user.credibility_score = 90.0

    mock_profile = MagicMock()
    mock_profile.status = "VERIFIED"
    mock_profile.is_online = True
    mock_profile.duty_status = DutyStatus.ONLINE_AVAILABLE
    mock_profile.acceptance_rate_30d = 95.0

    mock_eligibility_res = MagicMock()
    mock_eligibility_res.unique.return_value.all.return_value = [(mock_user, mock_profile)]
    engine.provider_profile_repo.execute = AsyncMock(return_value=mock_eligibility_res)

    candidates = await engine._fetch_and_filter_candidates(
        task=task,
        dispatch_session=searching_session,
    )

    assert len(candidates) == 1
    assert candidates[0].user_id == "provider-far"
    assert searching_session.search_radius_km == 20.0
    assert searching_session.search_offset == 0


@pytest.mark.asyncio
async def test_remove_provider_location_clears_last_known_location(mock_db_session):
    from app.core.models.users import UserLocation
    from app.core.services.provider_location import PostGISProviderLocationService

    location_repo = MagicMock()
    provider_profile_repo = MagicMock()

    loc = UserLocation(
        user_id="prov-1",
        latitude=6.5,
        longitude=3.4,
        last_known_location="0101000020E6100000...",
    )
    mock_res = MagicMock()
    mock_res.one_or_none.return_value = loc
    location_repo.execute = AsyncMock(return_value=mock_res)
    location_repo.add = AsyncMock()

    geo_service = PostGISProviderLocationService(
        location_repo=location_repo,
        provider_profile_repo=provider_profile_repo,
    )

    success = await geo_service.remove_provider_location("prov-1")
    assert success is True
    assert loc.latitude is None
    assert loc.longitude is None
    assert loc.last_known_location is None
    assert location_repo.add.called


@pytest.mark.asyncio
async def test_dispatch_to_candidate_skips_when_duty_status_race_lost(mock_db_session):
    engine = MatchingEngine(session_id="session-race", db_session=mock_db_session)
    candidate = _ScoredCandidate(user_id="prov-race", distance_km=1.0, score=10.0)
    task = Task(id="task-race", title="Race Task", description="Desc")

    # Simulate update returning rowcount = 0 (lost race to concurrent dispatch)
    mock_update_res = MagicMock()
    mock_update_res.rowcount = 0
    engine.provider_profile_repo.execute = AsyncMock(return_value=mock_update_res)
    engine.attempt_repo.add = AsyncMock()

    attempt = await engine._dispatch_to_candidate(
        candidate=candidate,
        task=task,
        dispatch_session_id="session-race",
        sequence_order=1,
    )

    assert attempt is None
    assert not engine.attempt_repo.add.called


@pytest.mark.asyncio
async def test_get_excluded_provider_ids_unpacks_row_tuples(mock_db_session):
    engine = MatchingEngine(session_id="session-tuple", db_session=mock_db_session)

    # Simulate execute returning SQLAlchemy Row tuples: ("prov-1",) and ("prov-2",)
    mock_res = MagicMock()
    mock_res.all.return_value = [("prov-1",), ("prov-2",), (None,)]
    engine.attempt_repo.execute = AsyncMock(return_value=mock_res)

    excluded = await engine._get_excluded_provider_ids(task_id="task-123")
    assert set(excluded) == {"prov-1", "prov-2"}





