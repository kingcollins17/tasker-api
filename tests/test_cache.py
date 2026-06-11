import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.core.services.cache import CacheService

@pytest.fixture(autouse=True)
def reset_cache_service_singleton():
    """Reset the singleton instance of CacheService before each test to ensure test isolation."""
    CacheService._instance = None
    yield
    CacheService._instance = None


@pytest.fixture
def mock_redis_client():
    # Mock Redis client returned by redis.asyncio.from_url
    client = MagicMock()
    client.get = AsyncMock()
    client.set = AsyncMock()
    client.delete = AsyncMock()
    client.exists = AsyncMock()
    client.flushdb = AsyncMock()
    client.close = AsyncMock()
    client.publish = AsyncMock()
    client.pubsub = MagicMock()
    return client


@pytest.fixture
def cache_service(mock_redis_client):
    # Patch from_url to return our mocked client
    with patch("app.core.services.cache.from_url", return_value=mock_redis_client):
        service = CacheService("redis://mock_url")
        return service

@pytest.mark.anyio
async def test_cache_get(cache_service, mock_redis_client):
    mock_redis_client.get.return_value = "cached_val"
    result = await cache_service.get("my_key")
    assert result == "cached_val"
    mock_redis_client.get.assert_called_once_with("my_key")

@pytest.mark.anyio
async def test_cache_set(cache_service, mock_redis_client):
    mock_redis_client.set.return_value = True
    result = await cache_service.set("my_key", "my_val", expire=3600)
    assert result is True
    mock_redis_client.set.assert_called_once_with("my_key", "my_val", ex=3600)

@pytest.mark.anyio
async def test_cache_delete(cache_service, mock_redis_client):
    mock_redis_client.delete.return_value = 1
    result = await cache_service.delete("my_key")
    assert result is True
    mock_redis_client.delete.assert_called_once_with("my_key")

@pytest.mark.anyio
async def test_cache_delete_not_found(cache_service, mock_redis_client):
    mock_redis_client.delete.return_value = 0
    result = await cache_service.delete("missing_key")
    assert result is False

@pytest.mark.anyio
async def test_cache_exists(cache_service, mock_redis_client):
    mock_redis_client.exists.return_value = 1
    result = await cache_service.exists("my_key")
    assert result is True
    mock_redis_client.exists.assert_called_once_with("my_key")

@pytest.mark.anyio
async def test_cache_get_json(cache_service, mock_redis_client):
    mock_redis_client.get.return_value = '{"a": 1, "b": "hello"}'
    result = await cache_service.get_json("json_key")
    assert result == {"a": 1, "b": "hello"}

@pytest.mark.anyio
async def test_cache_get_json_invalid(cache_service, mock_redis_client):
    mock_redis_client.get.return_value = 'invalid_json'
    result = await cache_service.get_json("json_key")
    assert result is None

@pytest.mark.anyio
async def test_cache_set_json(cache_service, mock_redis_client):
    mock_redis_client.set.return_value = True
    result = await cache_service.set_json("json_key", {"a": 1}, expire=60)
    assert result is True
    mock_redis_client.set.assert_called_once_with("json_key", '{"a": 1}', ex=60)

@pytest.mark.anyio
async def test_cache_set_json_invalid_type(cache_service):
    # Try setting something that cannot be serialized (e.g. a set)
    result = await cache_service.set_json("json_key", {1, 2, 3})
    assert result is False

@pytest.mark.anyio
async def test_cache_flush_all(cache_service, mock_redis_client):
    mock_redis_client.flushdb.return_value = True
    result = await cache_service.flush_all()
    assert result is True
    mock_redis_client.flushdb.assert_called_once()

@pytest.mark.anyio
async def test_cache_close(cache_service, mock_redis_client):
    await cache_service.close()
    mock_redis_client.close.assert_called_once()


@pytest.mark.anyio
async def test_cache_publish(cache_service, mock_redis_client):
    mock_redis_client.publish.return_value = 2
    result = await cache_service.publish("my_channel", "hello")
    assert result == 2
    mock_redis_client.publish.assert_called_once_with("my_channel", "hello")


def test_cache_pubsub(cache_service, mock_redis_client):
    from redis.asyncio.client import PubSub
    mock_pubsub = MagicMock(spec=PubSub)
    mock_redis_client.pubsub.return_value = mock_pubsub
    
    result = cache_service.pubsub()
    assert result == mock_pubsub
    mock_redis_client.pubsub.assert_called_once()


def test_cache_singleton():
    """Verify that multiple instantiations of CacheService return the same instance."""
    CacheService._instance = None
    
    with patch("app.core.services.cache.from_url") as mock_from_url:
        service1 = CacheService("redis://mock_url_1")
        service2 = CacheService("redis://mock_url_2")
        
        assert service1 is service2
        mock_from_url.assert_called_once_with("redis://mock_url_1", decode_responses=True)


def test_get_cache_service_dependency():
    """Verify get_cache_service dependency returns a cached CacheService instance."""
    from app.core.services import get_cache_service
    get_cache_service.cache_clear()
    
    with patch("app.core.services.cache.CacheService") as mock_cache_class:
        mock_instance = MagicMock()
        mock_cache_class.return_value = mock_instance
        
        service1 = get_cache_service()
        service2 = get_cache_service()
        
        assert service1 is service2
        assert service1 == mock_instance
        mock_cache_class.assert_called_once()



