import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from fastapi import status

from app.main import app
from app.core.models.tasks import (
    Task,
    TaskLocation,
    TaskBid,
    TaskAssignment,
    TaskStatusHistory,
    TaskAttachment,
    TaskStatus,
    TaskBidStatus,
    TaskAssignmentStatus,
)
from app.core.models.users import UserType
from app.features.tasks.services import TaskService, get_task_service
from app.features.tasks.schemas import TaskCreate, TaskUpdate, TaskBidCreate
from app.features.users.schemas import UserResponse
from app.core.repository import Repository
from app.core.deps.auth import GetCurrentUser
from app.features.tasks.router.bids import get_current_provider

# Test Users
now_dt = datetime.now(timezone.utc)

MOCK_CUSTOMER = UserResponse(
    id="customer-1",
    email="customer@example.com",
    type=UserType.CUSTOMER,
    is_active=True,
    email_verified=True,
    phone_verified=True,
    created_at=now_dt,
    updated_at=now_dt,
)

MOCK_PROVIDER = UserResponse(
    id="provider-1",
    email="provider@example.com",
    type=UserType.PROVIDER,
    is_active=True,
    email_verified=True,
    phone_verified=True,
    created_at=now_dt,
    updated_at=now_dt,
)


# Fixtures
@pytest.fixture
def mock_task_repo():
    repo = MagicMock(spec=Repository)
    repo.session = MagicMock()
    repo.session.bind = MagicMock()
    repo.session.bind.dialect.name = "postgresql"
    repo.add = AsyncMock(side_effect=lambda x: x)
    repo.get = AsyncMock()
    repo.update = AsyncMock()
    repo.refresh = AsyncMock()
    repo.execute = AsyncMock()
    return repo

@pytest.fixture
def mock_location_repo():
    repo = MagicMock(spec=Repository)
    repo.add = AsyncMock(side_effect=lambda x: x)
    repo.update = AsyncMock()
    return repo

@pytest.fixture
def mock_bid_repo():
    repo = MagicMock(spec=Repository)
    repo.add = AsyncMock(side_effect=lambda x: x)
    repo.get = AsyncMock()
    repo.get_all = AsyncMock(return_value=[])
    repo.update = AsyncMock()
    return repo

@pytest.fixture
def mock_assignment_repo():
    repo = MagicMock(spec=Repository)
    repo.add = AsyncMock(side_effect=lambda x: x)
    return repo

@pytest.fixture
def mock_history_repo():
    repo = MagicMock(spec=Repository)
    repo.add = AsyncMock(side_effect=lambda x: x)
    return repo

@pytest.fixture
def mock_attachment_repo():
    repo = MagicMock(spec=Repository)
    return repo

@pytest.fixture
def mock_user_repo():
    repo = MagicMock(spec=Repository)
    repo.get = AsyncMock(return_value=MOCK_CUSTOMER)
    repo.add = AsyncMock(side_effect=lambda x: x)
    repo.update = AsyncMock()
    return repo

@pytest.fixture
def task_service(
    mock_task_repo,
    mock_location_repo,
    mock_bid_repo,
    mock_assignment_repo,
    mock_history_repo,
    mock_attachment_repo,
    mock_user_repo,
):
    return TaskService(
        task_repo=mock_task_repo,
        location_repo=mock_location_repo,
        bid_repo=mock_bid_repo,
        assignment_repo=mock_assignment_repo,
        history_repo=mock_history_repo,
        attachment_repo=mock_attachment_repo,
        user_repo=mock_user_repo,
    )


@pytest.fixture
def client(task_service, monkeypatch):
    import functools
    from app.core.deps.auth import GetCurrentUser
    
    app.dependency_overrides[get_task_service] = lambda: task_service
    
    original_call = GetCurrentUser.__call__
    @functools.wraps(original_call)
    async def mock_get_current_user(self, *args, **kwargs):
        from app.core.models.users import UserType
        if getattr(self, "required_type", None) == UserType.PROVIDER:
            return MOCK_PROVIDER
        return MOCK_CUSTOMER
        
    monkeypatch.setattr("app.core.deps.auth.GetCurrentUser.__call__", mock_get_current_user)
    
    yield TestClient(app)
    app.dependency_overrides.clear()


# Service Level Tests
@pytest.mark.asyncio
async def test_service_create_task(task_service, mock_task_repo, mock_location_repo, mock_history_repo):
    # Arrange
    schema = TaskCreate(
        title="Fix Plumber",
        description="Leaking kitchen sink",
        category_id="cat-1",
        service_id="srv-1",
        budget_min=5000.0,
        budget_max=10000.0,
        latitude=6.5244,
        longitude=3.3792,
        address="123 Broad St",
        city="Lagos",
        state="Lagos",
        country="Nigeria",
    )

    # Act
    task = await task_service.create_task("customer-1", schema)

    # Assert
    assert task.title == "Fix Plumber"
    assert task.status == TaskStatus.OPEN
    mock_task_repo.add.assert_called_once()
    mock_location_repo.add.assert_called_once()
    mock_history_repo.add.assert_called_once()

@pytest.mark.asyncio
async def test_service_list_tasks_spatial_sqlite(task_service, mock_task_repo):
    # Arrange
    # Mock dialect bind to SQLite for spatial testing fallback
    mock_bind = MagicMock()
    mock_bind.dialect.name = "sqlite"
    mock_task_repo.session.bind = mock_bind
    
    mock_result = MagicMock()
    mock_result.first.return_value = 10
    mock_result.all.return_value = []
    mock_task_repo.execute.return_value = mock_result

    # Act
    tasks, total = await task_service.get_tasks(
        latitude=6.5244,
        longitude=3.3792,
        radius_km=15.0,
    )

    # Assert
    assert total == 10
    assert mock_task_repo.execute.call_count == 2 # 1 for count, 1 for select

@pytest.mark.asyncio
async def test_service_create_bid(task_service, mock_task_repo, mock_bid_repo, mock_history_repo):
    # Arrange
    task = Task(id="task-123", customer_id="customer-1", status=TaskStatus.OPEN, title="Task", description="Desc")
    mock_task_repo.get.return_value = task
    mock_bid_repo.get_all.return_value = []

    schema = TaskBidCreate(price=7500.0, message="I can do it today", estimated_duration="2 hours")

    # Act
    bid = await task_service.create_bid("task-123", "provider-1", schema)

    # Assert
    assert bid.price == 7500.0
    assert bid.status == TaskBidStatus.PENDING
    mock_task_repo.update.assert_called_once()
    args, kwargs = mock_task_repo.update.call_args
    assert args[0] == "task-123"
    assert args[1]["status"] == TaskStatus.BIDDING

@pytest.mark.asyncio
async def test_service_accept_bid(task_service, mock_task_repo, mock_bid_repo, mock_assignment_repo):
    # Arrange
    bid = TaskBid(id="bid-456", task_id="task-123", provider_id="provider-1", price=7000.0, status=TaskBidStatus.PENDING)
    task = Task(id="task-123", customer_id="customer-1", status=TaskStatus.BIDDING, title="Task", description="Desc")
    
    mock_bid_repo.get.return_value = bid
    mock_task_repo.get.return_value = task
    mock_bid_repo.get_all.return_value = [bid] # only our bid is pending

    # Act
    assignment = await task_service.accept_bid("bid-456", "customer-1")

    # Assert
    assert assignment.task_id == "task-123"
    assert assignment.provider_id == "provider-1"
    assert assignment.accepted_price == 7000.0
    
    mock_bid_repo.update.assert_called_once()
    bid_args, _ = mock_bid_repo.update.call_args
    assert bid_args[0] == "bid-456"
    assert bid_args[1]["status"] == TaskBidStatus.ACCEPTED

    mock_task_repo.update.assert_called_once()
    task_args, _ = mock_task_repo.update.call_args
    assert task_args[0] == "task-123"
    assert task_args[1]["status"] == TaskStatus.ASSIGNED



# API Router Tests
def test_api_create_task(client, task_service, mock_task_repo):
    # Arrange
    task_service.create_task = AsyncMock(return_value=Task(
        id="task-1",
        customer_id="customer-1",
        title="Help with painting",
        description="Paint kitchen walls",
        status=TaskStatus.OPEN,
        location=TaskLocation(latitude=6.5, longitude=3.4)
    ))

    payload = {
        "title": "Help with painting",
        "description": "Paint kitchen walls",
        "latitude": 6.5,
        "longitude": 3.4,
    }

    # Act
    response = client.post("/api/v1/tasks", json=payload)

    # Assert
    assert response.status_code == status.HTTP_201_CREATED
    json_data = response.json()
    assert json_data["status_code"] == 201
    assert json_data["data"]["title"] == "Help with painting"

def test_api_list_tasks(client, task_service):
    # Arrange
    task_service.get_tasks = AsyncMock(return_value=([
        Task(
            id="task-1",
            customer_id="customer-1",
            title="Clean windows",
            description="3 windows",
            status=TaskStatus.OPEN
        )
    ], 1))

    # Act
    response = client.get("/api/v1/tasks?latitude=6.5&longitude=3.4&radius_km=10")

    # Assert
    assert response.status_code == status.HTTP_200_OK
    json_data = response.json()
    assert len(json_data["data"]["items"]) == 1
    assert json_data["data"]["items"][0]["title"] == "Clean windows"

def test_api_create_bid(client, task_service):
    # Arrange
    task_service.create_bid = AsyncMock(return_value=TaskBid(
        id="bid-1",
        task_id="task-1",
        provider_id="provider-1",
        price=15000.0,
        status=TaskBidStatus.PENDING
    ))

    payload = {
        "price": 15000.0,
        "message": "Can do it",
    }

    # Act
    response = client.post("/api/v1/tasks/task-1/bids", json=payload)

    # Assert
    assert response.status_code == status.HTTP_201_CREATED
    json_data = response.json()
    assert json_data["data"]["price"] == 15000.0

def test_api_accept_bid(client, task_service):
    # Arrange
    task_service.accept_bid = AsyncMock(return_value=TaskAssignment(
        id="assign-1",
        task_id="task-1",
        provider_id="provider-1",
        accepted_price=12000.0,
        status=TaskAssignmentStatus.ASSIGNED
    ))

    # Act
    response = client.post("/api/v1/bids/bid-1/accept")

    # Assert
    assert response.status_code == status.HTTP_200_OK
    json_data = response.json()
    assert json_data["data"]["accepted_price"] == 12000.0
