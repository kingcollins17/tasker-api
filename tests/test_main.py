import pytest
from httpx import AsyncClient

@pytest.mark.anyio
async def test_read_root(client: AsyncClient):
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Tasker API"
    assert data["status"] == "healthy"

@pytest.mark.anyio
async def test_health_check(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
