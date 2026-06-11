import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.fixture(autouse=True)
def mock_init_db():
    with patch("app.main.init_db", new_callable=AsyncMock) as mock:
        yield mock

@pytest.fixture(autouse=True)
def mock_cache_close():
    with patch("app.main.get_cache_service") as mock_get_cache:
        mock_instance = MagicMock()
        mock_instance.close = AsyncMock()
        mock_get_cache.return_value = mock_instance
        yield mock_get_cache


@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

