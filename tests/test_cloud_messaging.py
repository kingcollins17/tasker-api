import pytest
from app.core.services.cloud_messaging import CloudMessagingService, MockCloudMessagingService, get_cloud_messaging_service

def test_cloud_messaging_service_is_abstract():
    with pytest.raises(TypeError):
        CloudMessagingService()  # type: ignore


@pytest.mark.asyncio
async def test_mock_cloud_messaging_service_send_message():
    # Arrange
    service = MockCloudMessagingService()
    token = "mock-fcm-token"
    
    # Act
    result = await service.send_message(
        token=token,
        title="Test Notification",
        body="This is a test notification body",
        data={"key": "value"}
    )
    
    # Assert
    assert result is True


@pytest.mark.asyncio
async def test_mock_cloud_messaging_service_send_multicast():
    # Arrange
    service = MockCloudMessagingService()
    tokens = ["mock-fcm-token-1", "mock-fcm-token-2"]
    
    # Act
    result = await service.send_multicast(
        tokens=tokens,
        title="Multicast Notification",
        body="This is a multicast body",
        data={"badge": "1"}
    )
    
    # Assert
    assert len(result) == 2
    assert result["mock-fcm-token-1"] is True
    assert result["mock-fcm-token-2"] is True


def test_get_cloud_messaging_service_dependency():
    # Act
    service = get_cloud_messaging_service()
    
    # Assert
    assert isinstance(service, CloudMessagingService)
    assert isinstance(service, MockCloudMessagingService)
