import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException, status
from sqlmodel import select

from app.features.users.router.profile import (
    bulk_attach_provider_services,
    bulk_remove_provider_services,
)
from app.features.users.schemas import BulkProviderServices, UserResponse
from app.core.models.users import UserType
from app.core.models.services import Service, ProviderServiceLink
from app.core.repository import Repository
from app.core.services.logger_service import LoggerService

@pytest.fixture
def mock_user():
    return UserResponse(
        id="provider-user-123",
        email="provider@example.com",
        type=UserType.PROVIDER,
        is_active=True,
        email_verified=True,
        phone_verified=True,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z"
    )

@pytest.fixture
def mock_logger():
    logger = MagicMock(spec=LoggerService)
    logger.metric = AsyncMock()
    logger.warn = AsyncMock()
    logger.error = AsyncMock()
    return logger


@pytest.mark.asyncio
async def test_bulk_attach_provider_services_success(mock_user, mock_logger):
    service_repo = MagicMock(spec=Repository)
    link_repo = MagicMock(spec=Repository)
    response_mock = MagicMock()

    s1 = Service(id="svc-1", name="Plumbing", is_active=True)
    s2 = Service(id="svc-2", name="Electrical", is_active=True)

    svc_result = MagicMock()
    svc_result.all.return_value = [s1, s2]
    service_repo.execute = AsyncMock(return_value=svc_result)

    link_result = MagicMock()
    link_result.all.return_value = []
    link_repo.execute = AsyncMock(return_value=link_result)
    link_repo.bulk_add = AsyncMock()

    schema = BulkProviderServices(service_ids=["svc-1", "svc-2"])

    res = await bulk_attach_provider_services(
        schema=schema,
        response=response_mock,
        current_user=mock_user,
        service_repo=service_repo,
        link_repo=link_repo,
        system_logger=mock_logger
    )

    assert res.status_code == status.HTTP_200_OK
    assert res.detail == "Services successfully added."
    link_repo.bulk_add.assert_called_once()


@pytest.mark.asyncio
async def test_bulk_attach_provider_services_exceeds_max(mock_user, mock_logger):
    service_repo = MagicMock(spec=Repository)
    link_repo = MagicMock(spec=Repository)
    response_mock = MagicMock()

    s1 = Service(id="svc-1", is_active=True)
    s2 = Service(id="svc-2", is_active=True)
    s3 = Service(id="svc-3", is_active=True)
    s4 = Service(id="svc-4", is_active=True)

    svc_result = MagicMock()
    svc_result.all.return_value = [s1, s2, s3, s4]
    service_repo.execute = AsyncMock(return_value=svc_result)

    link_result = MagicMock()
    link_result.all.return_value = []
    link_repo.execute = AsyncMock(return_value=link_result)

    schema = BulkProviderServices(service_ids=["svc-1", "svc-2", "svc-3", "svc-4"])

    with pytest.raises(HTTPException) as exc_info:
        await bulk_attach_provider_services(
            schema=schema,
            response=response_mock,
            current_user=mock_user,
            service_repo=service_repo,
            link_repo=link_repo,
            system_logger=mock_logger
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "maximum of 3 services" in exc_info.value.detail


@pytest.mark.asyncio
async def test_bulk_remove_provider_services_success(mock_user, mock_logger):
    link_repo = MagicMock(spec=Repository)
    response_mock = MagicMock()

    link_repo.execute = AsyncMock()
    link_repo.commit = AsyncMock()

    schema = BulkProviderServices(service_ids=["svc-1", "svc-2"])

    res = await bulk_remove_provider_services(
        schema=schema,
        response=response_mock,
        current_user=mock_user,
        link_repo=link_repo,
        system_logger=mock_logger
    )

    assert res.status_code == status.HTTP_200_OK
    assert res.detail == "Services successfully removed."
    link_repo.execute.assert_called_once()
    link_repo.commit.assert_called_once()
