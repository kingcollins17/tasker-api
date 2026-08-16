import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from fastapi import status, HTTPException
from app.core.models.tasks import Task, TaskAssignment, TaskAssignmentStatus
from app.core.models.users import User, UserType, ProviderProfile
from app.features.tasks.router.assignments import verify_provider_pin, PinBody


@pytest.mark.asyncio
async def test_verify_provider_pin_success():
    mock_task_repo = AsyncMock()
    mock_user_repo = AsyncMock()
    mock_logger = AsyncMock()

    customer_user = MagicMock()
    customer_user.id = "cust-123"
    customer_user.user_type = UserType.CUSTOMER

    assignment = TaskAssignment(
        id="assign-1",
        task_id="task-1",
        provider_id="prov-456",
        pin="4321",
        status=TaskAssignmentStatus.ASSIGNED,
    )

    task = Task(
        id="task-1",
        customer_id="cust-123",
        title="Fix Plumbing",
    )
    task.assignment = assignment

    provider_user = User(
        id="prov-456",
        email="provider@example.com",
        first_name="John",
        last_name="Doe",
        user_type=UserType.PROVIDER,
    )
    provider_profile = ProviderProfile(
        user_id="prov-456",
        first_name="John",
        last_name="Doe",
        rating=4.8,
    )
    provider_user.provider_profile = provider_profile

    mock_task_repo.get.return_value = task
    mock_user_repo.get.return_value = provider_user

    body = PinBody(pin="4321")
    response = await verify_provider_pin(
        task_id="task-1",
        body=body,
        current_user=customer_user,
        task_repo=mock_task_repo,
        user_repo=mock_user_repo,
        system_logger=mock_logger,
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data is not None
    assert response.data.id == "prov-456"
    assert response.data.fullname == "John Doe"


@pytest.mark.asyncio
async def test_verify_provider_pin_mismatch():
    mock_task_repo = AsyncMock()
    mock_user_repo = AsyncMock()
    mock_logger = AsyncMock()

    customer_user = MagicMock()
    customer_user.id = "cust-123"
    customer_user.user_type = UserType.CUSTOMER

    assignment = TaskAssignment(
        id="assign-1",
        task_id="task-1",
        provider_id="prov-456",
        pin="4321",
        status=TaskAssignmentStatus.ASSIGNED,
    )

    task = Task(
        id="task-1",
        customer_id="cust-123",
        title="Fix Plumbing",
    )
    task.assignment = assignment

    mock_task_repo.get.return_value = task

    body = PinBody(pin="9999")
    with pytest.raises(HTTPException) as exc_info:
        await verify_provider_pin(
            task_id="task-1",
            body=body,
            current_user=customer_user,
            task_repo=mock_task_repo,
            user_repo=mock_user_repo,
            system_logger=mock_logger,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "imposter" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_verify_provider_pin_unauthorized_user():
    mock_task_repo = AsyncMock()
    mock_user_repo = AsyncMock()
    mock_logger = AsyncMock()

    other_user = MagicMock()
    other_user.id = "cust-999"
    other_user.user_type = UserType.CUSTOMER

    task = Task(
        id="task-1",
        customer_id="cust-123",
        title="Fix Plumbing",
    )

    mock_task_repo.get.return_value = task

    body = PinBody(pin="4321")
    with pytest.raises(HTTPException) as exc_info:
        await verify_provider_pin(
            task_id="task-1",
            body=body,
            current_user=other_user,
            task_repo=mock_task_repo,
            user_repo=mock_user_repo,
            system_logger=mock_logger,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
