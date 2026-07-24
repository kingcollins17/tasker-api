from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.models.credibility import CredibilityLedgerEntry, CredibilityReason
from app.core.repository import Repository
from app.features.credibility.services import CredibilityService


@pytest.fixture
def mock_ledger_repo():
    repo = MagicMock(spec=Repository)
    repo.add = AsyncMock()
    repo.execute = AsyncMock()
    return repo


@pytest.mark.asyncio
async def test_add_credibility_entry_triggers_celery_task(mock_ledger_repo):
    service = CredibilityService(ledger_repo=mock_ledger_repo)

    mock_entry = CredibilityLedgerEntry(
        id="ledger-123",
        user_id="user-456",
        delta=3.0,
        reason=CredibilityReason.TASK_COMPLETED,
    )
    mock_ledger_repo.add.return_value = mock_entry

    with patch(
        "app.features.credibility.services.sync_user_credibility_score"
    ) as mock_sync_task:
        entry = await service.add_credibility_entry(
            user_id="user-456",
            reason=CredibilityReason.TASK_COMPLETED,
            task_id="task-789",
        )

        assert entry is not None
        assert entry.delta == 3.0
        assert entry.reason == CredibilityReason.TASK_COMPLETED
        mock_ledger_repo.add.assert_called_once()
        mock_sync_task.delay.assert_called_once_with("user-456")


@pytest.mark.asyncio
async def test_add_credibility_entry_zero_delta_skipped(mock_ledger_repo):
    service = CredibilityService(ledger_repo=mock_ledger_repo)

    with patch(
        "app.features.credibility.services.sync_user_credibility_score"
    ) as mock_sync_task:
        entry = await service.add_credibility_entry(
            user_id="user-456",
            reason=CredibilityReason.THREE_STAR_REVIEW,
        )

        assert entry is None
        mock_ledger_repo.add.assert_not_called()
        mock_sync_task.delay.assert_not_called()
