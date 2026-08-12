import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, time

from app.core.models.users import ProviderAvailability
from app.core.services.availability_service import AvailabilityService, DAY_NAMES

@pytest.fixture
def mock_repos():
    avail_repo = MagicMock()
    avail_repo.execute = AsyncMock()
    avail_repo.get = AsyncMock()
    avail_repo.add = AsyncMock()

    loc_repo = MagicMock()
    loc_repo.execute = AsyncMock()
    return avail_repo, loc_repo

@pytest.mark.asyncio
async def test_create_default_availability(mock_repos):
    avail_repo, loc_repo = mock_repos
    service = AvailabilityService(avail_repo, loc_repo)

    # Return empty list for existing availability
    mock_exec_res = MagicMock()
    mock_exec_res.all.return_value = []
    avail_repo.execute.return_value = mock_exec_res
    avail_repo.add.side_effect = lambda block: block

    blocks = await service.create_default_availability("provider-1")
    assert len(blocks) == 7
    for idx, block in enumerate(blocks, start=1):
        assert block.provider_id == "provider-1"
        assert block.day_of_week == idx
        assert block.day_name == DAY_NAMES[idx]
        assert block.is_active is True

@pytest.mark.asyncio
async def test_update_availability_block(mock_repos):
    avail_repo, loc_repo = mock_repos
    service = AvailabilityService(avail_repo, loc_repo)

    existing_block = ProviderAvailability(
        id="block-1",
        provider_id="provider-1",
        day_of_week=1,
        day_name="Sunday",
        start_time=time(6, 0),
        end_time=time(23, 59),
        is_active=True
    )
    avail_repo.get.return_value = existing_block
    avail_repo.add.side_effect = lambda block: block

    updated = await service.update_availability_block(
        availability_id="block-1",
        provider_id="provider-1",
        day_of_week=2,
        is_active=False
    )

    assert updated.day_of_week == 2
    assert updated.day_name == "Monday"
    assert updated.is_active is False
