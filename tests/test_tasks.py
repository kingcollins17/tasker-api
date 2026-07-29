import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from fastapi import status

from app.main import app
from app.core.models.tasks import (
    Task,
    TaskLocation,
    TaskDispatchAttempt,
    TaskAssignment,
    TaskStatusHistory,
    TaskAttachment,
    TaskStatus,
    TaskAssignmentStatus,
    LocationType,
)
from app.core.models.users import UserType
from app.features.tasks.services import TaskService, get_task_service
from app.features.tasks.schemas import TaskCreate, TaskUpdate, LocationCreate
from app.features.users.schemas import UserResponse
from app.core.repository import Repository
from app.core.deps.auth import GetCurrentUser

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
def mock_attempt_repo():
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
def mock_transaction_repo():
    repo = MagicMock(spec=Repository)
    return repo

@pytest.fixture
def mock_service_repo():
    repo = MagicMock(spec=Repository)
    return repo

@pytest.fixture
def mock_payment_gateway():
    gateway = MagicMock()
    return gateway

@pytest.fixture
def mock_notification_service():
    service = MagicMock()
    return service

@pytest.fixture
def mock_pricing_engine():
    engine = MagicMock()
    from app.features.services.pricing_engine import PricingBreakdown
    engine.calculate_price = AsyncMock(return_value=PricingBreakdown())
    return engine

@pytest.fixture
def task_service(
    mock_task_repo,
    mock_location_repo,
    mock_attempt_repo,
    mock_assignment_repo,
    mock_history_repo,
    mock_attachment_repo,
    mock_user_repo,
    mock_transaction_repo,
    mock_service_repo,
    mock_payment_gateway,
    mock_notification_service,
    mock_pricing_engine,
):
    return TaskService(
        task_repo=mock_task_repo,
        location_repo=mock_location_repo,
        attempt_repo=mock_attempt_repo,
        assignment_repo=mock_assignment_repo,
        history_repo=mock_history_repo,
        attachment_repo=mock_attachment_repo,
        user_repo=mock_user_repo,
        transaction_repo=mock_transaction_repo,
        service_repo=mock_service_repo,
        payment_gateway=mock_payment_gateway,
        notification_service=mock_notification_service,
        pricing_engine=mock_pricing_engine,
    )


@pytest.fixture
def client(task_service, monkeypatch):
    import functools
    from app.core.deps.auth import GetCurrentUser
    
    app.dependency_overrides[get_task_service] = lambda: task_service
    monkeypatch.setattr("app.features.tasks.router.tasks.start_dispatch_workflow", MagicMock())
    
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
        locations=[
            LocationCreate(
                latitude=6.5244,
                longitude=3.3792,
                address="123 Broad St",
                city="Lagos",
                state="Lagos",
                country="Nigeria",
            )
        ],
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


# API Router Tests
def test_api_create_task(client, task_service):
    # Arrange
    task_service.create_task = AsyncMock(return_value=Task(
        id="task-1",
        customer_id="customer-1",
        title="Help with painting",
        description="Paint kitchen walls",
        status=TaskStatus.OPEN,
        locations=[TaskLocation(latitude=6.5, longitude=3.4)]
    ))

    payload = {
        "title": "Help with painting",
        "description": "Paint kitchen walls",
        "locations": [
            {
                "latitude": 6.5,
                "longitude": 3.4,
            }
        ],
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


@pytest.mark.asyncio
async def test_get_my_assignments_attaches_minimal_task():
    from app.features.tasks.router.assignments import get_my_assignments
    from app.core.models.tasks import TaskAssignment, Task, TaskStatus, TaskAssignmentStatus

    mock_assignment_repo = AsyncMock()
    
    mock_count_res = MagicMock()
    mock_count_res.one.return_value = 1
    
    task_inst = Task(
        id="task-100",
        title="Plumbing Job",
        description="Fix pipe leak",
        status=TaskStatus.ASSIGNED,
    )
    assignment_inst = TaskAssignment(
        id="assign-1",
        task_id="task-100",
        provider_id="provider-1",
        status=TaskAssignmentStatus.ASSIGNED,
    )
    
    mock_data_res = MagicMock()
    mock_data_res.unique.return_value.all.return_value = [(assignment_inst, task_inst)]
    
    mock_assignment_repo.execute.side_effect = [mock_count_res, mock_data_res]
    mock_logger = AsyncMock()
    
    resp = await get_my_assignments(
        page=1,
        per_page=20,
        status_filter=None,
        task_id=None,
        sort_by="assigned_at",
        sort_desc=True,
        current_user=MOCK_PROVIDER,
        assignment_repo=mock_assignment_repo,
        system_logger=mock_logger,
    )
    
    assert resp.status_code == status.HTTP_200_OK
    assert resp.data.total == 1
    item = resp.data.items[0]
    assert item.id == "assign-1"
    assert item.task is not None
    assert item.task.id == "task-100"
    assert item.task.title == "Plumbing Job"
    assert item.task.status == TaskStatus.ASSIGNED


@pytest.mark.asyncio
async def test_get_task_assignment_attaches_minimal_provider():
    from app.features.tasks.router.assignments import get_task_assignment
    from app.core.models.tasks import TaskAssignment, Task, TaskStatus, TaskAssignmentStatus
    from app.core.models.users import User

    mock_task_repo = AsyncMock()
    mock_assignment_repo = AsyncMock()
    mock_user_repo = AsyncMock()

    task_inst = Task(
        id="task-100",
        customer_id="customer-1",
        title="Plumbing Job",
        status=TaskStatus.ASSIGNED,
    )
    assignment_inst = TaskAssignment(
        id="assign-1",
        task_id="task-100",
        provider_id="provider-1",
        status=TaskAssignmentStatus.ASSIGNED,
    )
    provider_inst = User(
        id="provider-1",
        email="provider@example.com",
        phone_number="1234567890",
        average_ratings=4.8,
        credibility_score=95.0,
    )

    mock_task_repo.get.return_value = task_inst

    mock_exec_res = MagicMock()
    mock_exec_res.first.return_value = assignment_inst
    mock_assignment_repo.execute.return_value = mock_exec_res

    mock_user_repo.get.return_value = provider_inst
    mock_logger = AsyncMock()

    resp = await get_task_assignment(
        task_id="task-100",
        current_user=MOCK_CUSTOMER,
        assignment_repo=mock_assignment_repo,
        task_repo=mock_task_repo,
        user_repo=mock_user_repo,
        system_logger=mock_logger,
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data.id == "assign-1"
    assert resp.data.provider is not None
    assert resp.data.provider.id == "provider-1"
    assert resp.data.provider.email == "provider@example.com"


def test_complete_task_assignment_reruns_metrics(monkeypatch):
    mock_complete_async = AsyncMock(return_value=("srv-1", "cat-1"))

    mock_sync_provider = MagicMock()
    mock_sync_service = MagicMock()
    monkeypatch.setattr(
        "app.features.tasks.celery.dispatch._complete_task_assignment_async",
        mock_complete_async,
    )
    mock_process_payment = MagicMock()
    monkeypatch.setattr(
        "app.features.tasks.celery.dispatch.process_task_payment",
        mock_process_payment,
    )
    monkeypatch.setattr(
        "app.features.tasks.celery.dispatch.sync_provider_metrics",
        mock_sync_provider,
    )
    monkeypatch.setattr(
        "app.features.tasks.celery.dispatch.sync_service_metrics",
        mock_sync_service,
    )

    from app.features.tasks.celery.dispatch import complete_task_assignment

    complete_task_assignment("task-123", "provider-456")

    mock_complete_async.assert_called_once_with("task-123", "provider-456", "cash")
    mock_process_payment.delay.assert_called_once_with("task-123", "provider-456", "cash")
    mock_sync_provider.delay.assert_called_once_with("provider-456")
    mock_sync_service.delay.assert_called_once_with(service_id="srv-1", category_id="cat-1")


@pytest.mark.asyncio
async def test_sync_single_service_duration():
    from datetime import timedelta
    from app.features.tasks.celery.metrics import _sync_single_service_duration
    from app.core.models.services import Service

    mock_service_repo = MagicMock(spec=Repository)
    mock_assignment_repo = MagicMock(spec=Repository)

    now = datetime.now(timezone.utc)
    started_at = now - timedelta(minutes=45)
    completed_at = now

    assignment = MagicMock(spec=TaskAssignment)
    assignment.started_at = started_at
    assignment.completed_at = completed_at

    mock_result = MagicMock()
    mock_result.all.return_value = [assignment]
    mock_assignment_repo.execute.return_value = mock_result

    service = Service(id="srv-1", name="Plumbing", default_duration_min=60)
    mock_srv_result = MagicMock()
    mock_srv_result.scalar_one_or_none.return_value = service
    mock_service_repo.execute.return_value = mock_srv_result
    mock_service_repo.add = AsyncMock(side_effect=lambda x: x)

    avg_dur = await _sync_single_service_duration("srv-1", mock_service_repo, mock_assignment_repo)

    assert avg_dur == 45.0
    assert service.default_duration_min == 45
    mock_service_repo.add.assert_called_once_with(service)


@pytest.mark.asyncio
async def test_sync_single_category_duration():
    from datetime import timedelta
    from app.features.tasks.celery.metrics import _sync_single_category_duration
    from app.core.models.services import ServiceCategory

    mock_category_repo = MagicMock(spec=Repository)
    mock_assignment_repo = MagicMock(spec=Repository)

    now = datetime.now(timezone.utc)
    started_at = now - timedelta(minutes=90)
    completed_at = now

    assignment = MagicMock(spec=TaskAssignment)
    assignment.started_at = started_at
    assignment.completed_at = completed_at

    mock_result = MagicMock()
    mock_result.all.return_value = [assignment]
    mock_assignment_repo.execute.return_value = mock_result

    category = ServiceCategory(id="cat-1", name="Home Repairs", default_duration_min=60)
    mock_cat_result = MagicMock()
    mock_cat_result.scalar_one_or_none.return_value = category
    mock_category_repo.execute.return_value = mock_cat_result
    mock_category_repo.add = AsyncMock(side_effect=lambda x: x)

    avg_dur = await _sync_single_category_duration("cat-1", mock_category_repo, mock_assignment_repo)

    assert avg_dur == 90.0
    assert category.default_duration_min == 90
    mock_category_repo.add.assert_called_once_with(category)


def test_api_get_task_price_breakdown(client, task_service):
    from app.features.services.pricing_engine import PricingBreakdown

    task_service.estimate_task_price = AsyncMock(
        return_value=PricingBreakdown(
            base_price=1000.0,
            distance_fee=300.0,
            time_fee=1200.0,
            urgency_fee=0.0,
            complexity_fee=0.0,
            surge_multiplier=1.0,
            subtotal=2500.0,
            customer_total_price=2500.0,
            platform_fee=375.0,
            provider_payout=2125.0,
            take_rate=0.15,
        )
    )

    payload = {
        "category_id": "cat-1",
        "service_id": "srv-1",
        "locations": [
            {"latitude": 6.5, "longitude": 3.4},
            {"latitude": 6.6, "longitude": 3.5},
        ],
        "is_urgent": False,
    }

    response = client.post("/api/v1/tasks/price-breakdown", json=payload)

    assert response.status_code == status.HTTP_200_OK
    json_data = response.json()
    assert json_data["status_code"] == 200
    assert json_data["data"]["customer_total_price"] == 2500.0
    assert json_data["data"]["base_price"] == 1000.0


@pytest.mark.asyncio
async def test_service_estimate_task_price(task_service):
    from app.features.tasks.schemas import TaskPriceEstimateRequest, LocationCreate
    from app.features.services.pricing_engine import PricingBreakdown

    mock_engine = MagicMock()
    mock_engine.calculate_price = AsyncMock(
        return_value=PricingBreakdown(
            base_price=500.0,
            customer_total_price=500.0,
        )
    )
    task_service.pricing_engine = mock_engine

    schema = TaskPriceEstimateRequest(
        category_id="cat-1",
        service_id="srv-1",
        locations=[
            LocationCreate(latitude=6.5, longitude=3.4),
            LocationCreate(latitude=6.6, longitude=3.5),
        ],
    )

    breakdown = await task_service.estimate_task_price(schema, customer_id="customer-1")

    assert breakdown.customer_total_price == 500.0
    mock_engine.calculate_price.assert_called_once()



