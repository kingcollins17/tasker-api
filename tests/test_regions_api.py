import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from fastapi import status

from app.main import app
from app.core.models.regions import Region
from app.core.repository import Repository
from app.features.regions.router import get_region_repo

@pytest.fixture
def mock_region_repo():
    repo = MagicMock(spec=Repository)
    repo.get_all = AsyncMock(return_value=[])
    return repo

@pytest.fixture
def client(mock_region_repo):
    app.dependency_overrides[get_region_repo] = lambda: mock_region_repo
    yield TestClient(app)
    app.dependency_overrides.clear()

def test_get_regions_empty(client, mock_region_repo):
    # Arrange
    mock_region_repo.get_all.return_value = []

    # Act
    response = client.get("/api/v1/regions/")

    # Assert
    assert response.status_code == status.HTTP_200_OK
    json_data = response.json()
    assert json_data["statusCode"] == 200
    assert json_data["detail"] == "Regions retrieved successfully."
    assert json_data["data"] == []
    mock_region_repo.get_all.assert_called_once()

def test_get_regions_with_data(client, mock_region_repo):
    # Arrange
    regions = [
        Region(
            id="reg-1",
            state="Lagos",
            address_line="123 Broad Street",
            is_active=True,
            total_providers=5,
            total_customers=10,
            total_tasks=2,
            total_staff=1
        ),
        Region(
            id="reg-2",
            state="Abuja",
            address_line="456 Capital Way",
            is_active=False,
            total_providers=0,
            total_customers=0,
            total_tasks=0,
            total_staff=0
        )
    ]
    mock_region_repo.get_all.return_value = regions

    # Act
    response = client.get("/api/v1/regions/")

    # Assert
    assert response.status_code == status.HTTP_200_OK
    json_data = response.json()
    assert json_data["statusCode"] == 200
    assert json_data["detail"] == "Regions retrieved successfully."
    assert len(json_data["data"]) == 2
    
    reg1 = json_data["data"][0]
    assert reg1["id"] == "reg-1"
    assert reg1["state"] == "Lagos"
    assert reg1["is_active"] is True
    assert reg1["total_providers"] == 5

    reg2 = json_data["data"][1]
    assert reg2["id"] == "reg-2"
    assert reg2["state"] == "Abuja"
    assert reg2["is_active"] is False
    assert reg2["total_providers"] == 0

def test_get_regions_error(client, mock_region_repo):
    # Arrange
    mock_region_repo.get_all.side_effect = Exception("Database connection failed")

    # Act
    response = client.get("/api/v1/regions/")

    # Assert
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    json_data = response.json()
    assert json_data["statusCode"] == 500
    assert "Database connection failed" in json_data["detail"]
