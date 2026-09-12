from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.models.tasks import (
    DispatchAttemptStatus,
    DispatchSession,
    DispatchSessionStatus,
    DispatchSessionTrigger,
    Task,
    TaskAssignment,
    TaskAssignmentStatus,
    TaskDispatchAttempt,
    TaskDispatchStatus,
    TaskStatus,
)
from app.core.models.users import DutyStatus, ProviderProfile
from app.core.services.dispatch_policy import DispatchPolicy
from app.core.utils.datetime_helper import lagos_now
from app.features.tasks.dispatch_service import DispatchService


# ---------------------------------------------------------------------------
# DispatchPolicy Tests
# ---------------------------------------------------------------------------


def test_dispatch_policy_auto_retry_limits():
    assert DispatchPolicy.can_auto_retry(0) is True
    assert DispatchPolicy.can_auto_retry(4) is True
    assert DispatchPolicy.can_auto_retry(5) is False
    assert DispatchPolicy.can_auto_retry(6) is False


def test_dispatch_policy_manual_redispatch_limits():
    assert DispatchPolicy.can_manual_redispatch(0) is True
    assert DispatchPolicy.can_manual_redispatch(2) is True
    assert DispatchPolicy.can_manual_redispatch(3) is False
    assert DispatchPolicy.can_manual_redispatch(4) is False


def test_dispatch_policy_retry_delays():
    assert DispatchPolicy.get_next_retry_delay(0) == timedelta(seconds=120)  # 2m
    assert DispatchPolicy.get_next_retry_delay(1) == timedelta(seconds=300)  # 5m
    assert DispatchPolicy.get_next_retry_delay(2) == timedelta(seconds=600)  # 10m
    assert DispatchPolicy.get_next_retry_delay(3) == timedelta(seconds=1200) # 20m
    assert DispatchPolicy.get_next_retry_delay(4) == timedelta(seconds=1800) # 30m
    # Clamped at max delay for counts >= len
    assert DispatchPolicy.get_next_retry_delay(10) == timedelta(seconds=1800)


def test_dispatch_policy_provider_exclusions():
    assert DispatchPolicy.should_exclude_provider_status(DispatchAttemptStatus.DECLINED) is True
    assert DispatchPolicy.should_exclude_provider_status(DispatchAttemptStatus.TIMEOUT) is True
    assert DispatchPolicy.should_exclude_provider_status(DispatchAttemptStatus.CANCELLED) is True
    assert DispatchPolicy.should_exclude_provider_status(DispatchAttemptStatus.PENDING) is False


# ---------------------------------------------------------------------------
# DispatchService Unit Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_deps():
    session = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    task_repo = MagicMock()
    task_repo.get = AsyncMock()
    task_repo.add = AsyncMock()
    task_repo.execute = AsyncMock()

    session_repo = MagicMock()
    session_repo.get = AsyncMock()
    session_repo.add = AsyncMock(side_effect=lambda obj: obj)
    session_repo.execute = AsyncMock()

    attempt_repo = MagicMock()
    attempt_repo.get = AsyncMock()
    attempt_repo.add = AsyncMock()
    attempt_repo.execute = AsyncMock()

    assignment_repo = MagicMock()
    assignment_repo.get = AsyncMock()
    assignment_repo.add = AsyncMock()
    assignment_repo.execute = AsyncMock()

    provider_profile_repo = MagicMock()
    provider_profile_repo.execute = AsyncMock()
    provider_profile_repo.add = AsyncMock()

    system_logger = MagicMock()
    system_logger.info = AsyncMock()
    system_logger.warn = AsyncMock()

    notification_service = MagicMock()
    notification_service.notify = AsyncMock()

    credibility_service = MagicMock()
    credibility_service.add = AsyncMock()

    task_service = MagicMock()
    task_service.log_task_event = AsyncMock()

    return {
        "session": session,
        "task_repo": task_repo,
        "session_repo": session_repo,
        "attempt_repo": attempt_repo,
        "assignment_repo": assignment_repo,
        "provider_profile_repo": provider_profile_repo,
        "system_logger": system_logger,
        "notification_service": notification_service,
        "credibility_service": credibility_service,
        "task_service": task_service,
    }


@pytest.mark.asyncio
async def test_dispatch_service_start_initial_dispatch(mock_deps):
    task = Task(id="task-init", title="Test Task", description="Desc", status=TaskStatus.OPEN, customer_id="cust-1")
    
    mock_res_lock = MagicMock()
    mock_res_lock.one_or_none.return_value = task
    mock_deps["task_repo"].execute.return_value = mock_res_lock

    mock_res_seq = MagicMock()
    mock_res_seq.one_or_none.return_value = (0,)
    mock_deps["session_repo"].execute.return_value = mock_res_seq

    service = DispatchService(**mock_deps)
    
    # Mock MatchingEngine.run to succeed
    from unittest.mock import patch
    from app.core.services.matching_engine import MatchingResult
    with patch("app.features.tasks.dispatch_service.MatchingEngine.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = MatchingResult(matched=True, candidates_count=1, attempts_created=1)
        ds = await service.start_initial_dispatch("task-init")

        assert ds is not None
        assert ds.trigger == DispatchSessionTrigger.INITIAL
        assert ds.sequence == 1
        assert task.dispatch_status == TaskDispatchStatus.DISPATCHING
        assert task.status == TaskStatus.SEARCHING


@pytest.mark.asyncio
async def test_dispatch_service_handle_no_match_schedules_retry(mock_deps):
    task = Task(id="task-nomatch", title="Task", description="D", auto_dispatch_count=0, customer_id="c1", status=TaskStatus.SEARCHING)
    session = DispatchSession(id="sess-1", task_id="task-nomatch", trigger=DispatchSessionTrigger.INITIAL, sequence=1, status=DispatchSessionStatus.RUNNING)

    mock_deps["task_repo"].get.return_value = task
    mock_deps["session_repo"].get.return_value = session

    service = DispatchService(**mock_deps)
    await service.handle_no_match(task_id="task-nomatch", session_id="sess-1", reason="No candidates")

    assert session.status == DispatchSessionStatus.FAILED
    assert task.dispatch_status == TaskDispatchStatus.RETRY_SCHEDULED
    assert task.next_dispatch_at is not None


@pytest.mark.asyncio
async def test_dispatch_service_auto_retry_exhaustion(mock_deps):
    task = Task(id="task-exh", title="Task", description="D", auto_dispatch_count=5, customer_id="c1", status=TaskStatus.SEARCHING)
    session = DispatchSession(id="sess-5", task_id="task-exh", trigger=DispatchSessionTrigger.AUTO_RETRY, sequence=5, status=DispatchSessionStatus.RUNNING)

    mock_deps["task_repo"].get.return_value = task
    mock_deps["session_repo"].get.return_value = session

    service = DispatchService(**mock_deps)
    await service.handle_no_match(task_id="task-exh", session_id="sess-5", reason="Max retries")

    assert session.status == DispatchSessionStatus.EXHAUSTED
    assert task.dispatch_status == TaskDispatchStatus.AUTO_EXHAUSTED
    assert task.status == TaskStatus.EXPIRED
    assert mock_deps["notification_service"].notify.called


@pytest.mark.asyncio
async def test_dispatch_service_manual_redispatch_success(mock_deps):
    task = Task(
        id="task-manual",
        customer_id="cust-1",
        assigned_provider_id="prov-1",
        status=TaskStatus.ASSIGNED,
        title="Manual Task",
        description="Desc",
        manual_dispatch_count=0,
        assignment=TaskAssignment(id="assign-1", task_id="task-manual", provider_id="prov-1"),
    )

    mock_res_lock = MagicMock()
    mock_res_lock.one_or_none.return_value = task
    mock_deps["task_repo"].execute.return_value = mock_res_lock

    mock_res_seq = MagicMock()
    mock_res_seq.one_or_none.return_value = (1,)
    mock_deps["session_repo"].execute.return_value = mock_res_seq
    mock_deps["task_repo"].refresh = AsyncMock()

    service = DispatchService(**mock_deps)

    from unittest.mock import patch
    from app.core.services.matching_engine import MatchingResult
    with patch("app.features.tasks.dispatch_service.MatchingEngine.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = MatchingResult(matched=True, candidates_count=1, attempts_created=1)
        res_task = await service.manual_redispatch(task_id="task-manual", current_user_id="cust-1", feedback="Provider late")

        assert res_task.manual_dispatch_count == 1
        assert res_task.assigned_provider_id is None
        assert res_task.status == TaskStatus.SEARCHING
        assert res_task.dispatch_status == TaskDispatchStatus.DISPATCHING


@pytest.mark.asyncio
async def test_dispatch_service_manual_redispatch_limit_exceeded(mock_deps):
    task = Task(
        id="task-limit",
        customer_id="cust-1",
        status=TaskStatus.SEARCHING,
        title="Limit Task",
        description="Desc",
        manual_dispatch_count=3,
    )

    mock_res_lock = MagicMock()
    mock_res_lock.one_or_none.return_value = task
    mock_deps["task_repo"].execute.return_value = mock_res_lock

    service = DispatchService(**mock_deps)

    with pytest.raises(HTTPException) as exc:
        await service.manual_redispatch(task_id="task-limit", current_user_id="cust-1")

    assert exc.value.status_code == 400
    assert "Maximum allowed customer redispatches" in exc.value.detail
