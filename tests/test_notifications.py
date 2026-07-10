import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.core.models.notifications import (
    Notification,
    NotificationRecipient,
    NotificationChannel,
    NotificationPreference,
    NotificationDelivery,
    NotificationType,
    NotificationPriority,
    RecipientStatus,
)
from app.features.notifications.services import NotificationService
from app.features.notifications.schemas import CreateNotification
from app.features.notifications.tasks import _process_batch, _fan_out_notification
from app.core.repository import Repository


@pytest.fixture
def mock_notification_repo():
    repo = MagicMock(spec=Repository)
    repo.add = AsyncMock(side_effect=lambda x: x)
    repo.refresh = AsyncMock()
    repo.get = AsyncMock()
    return repo


@pytest.fixture
def mock_recipient_repo():
    repo = MagicMock(spec=Repository)
    repo.add = AsyncMock(side_effect=lambda x: x)
    repo.execute = AsyncMock()
    repo.bulk_update = AsyncMock()
    return repo


@pytest.fixture
def mock_delivery_repo():
    repo = MagicMock(spec=Repository)
    repo.bulk_add = AsyncMock()
    return repo


@pytest.fixture
def mock_preference_repo():
    repo = MagicMock(spec=Repository)
    repo.execute = MagicMock()
    return repo


@pytest.fixture
def mock_user_repo():
    repo = MagicMock(spec=Repository)
    return repo


@pytest.fixture
def notification_service(
    mock_notification_repo,
    mock_recipient_repo,
    mock_delivery_repo,
    mock_preference_repo,
    mock_user_repo,
):
    return NotificationService(
        notification_repo=mock_notification_repo,
        recipient_repo=mock_recipient_repo,
        delivery_repo=mock_delivery_repo,
        preference_repo=mock_preference_repo,
        user_repo=mock_user_repo,
    )


@pytest.mark.asyncio
@patch("app.features.notifications.tasks.process_notification.delay")
async def test_create_notification_with_channels(
    mock_delay,
    notification_service,
    mock_notification_repo,
    mock_recipient_repo,
):
    # Arrange
    schema = CreateNotification(
        type=NotificationType.TASK_COMPLETED,
        title="Completed",
        body="Task is completed",
        recipient_ids=["user-1", "user-2"],
        channels=[NotificationChannel.EMAIL, NotificationChannel.PUSH],
    )

    # Act
    notification = await notification_service.create_notification(
        schema=schema, created_by="admin-1"
    )

    # Assert
    assert notification.channels == ["email", "push"]
    assert mock_notification_repo.add.call_count == 1
    assert mock_recipient_repo.add.call_count == 2
    mock_delay.assert_called_once_with(notification.id)


@pytest.mark.asyncio
@patch("app.features.notifications.tasks.send_email_batch.delay")
@patch("app.features.notifications.tasks.send_push_batch.delay")
@patch("app.features.notifications.tasks.send_sms_batch.delay")
@patch("app.core.database.async_session_maker")
async def test_process_batch_filtering_channels(
    mock_session_maker,
    mock_sms_delay,
    mock_push_delay,
    mock_email_delay,
):
    # Arrange
    mock_session = MagicMock()
    mock_session_maker.return_value.__aenter__.return_value = mock_session

    notification = Notification(
        id="notif-123",
        type=NotificationType.TASK_COMPLETED,
        title="Completed",
        body="Task completed",
        channels=["email", "push"],  # Restrict to email and push
    )

    recipient = NotificationRecipient(
        id="recip-1",
        notification_id="notif-123",
        recipient_id="user-123",
    )

    # Create mock repos
    mock_notification_repo = MagicMock()
    mock_notification_repo.get = AsyncMock(return_value=notification)

    mock_recipient_repo = MagicMock()
    mock_recipient_repo.get = AsyncMock(return_value=recipient)
    mock_recipient_repo.bulk_update = AsyncMock()

    mock_delivery_repo = MagicMock()
    mock_delivery_repo.bulk_add = AsyncMock()

    mock_preference_repo = MagicMock()
    # Mock preference query results (user has email, push, and sms preferences)
    mock_pref_result = MagicMock()
    mock_pref_result.all = MagicMock(return_value=[
        NotificationPreference(user_id="user-123", notification_type=NotificationType.TASK_COMPLETED, channel=NotificationChannel.EMAIL, enabled=True),
        NotificationPreference(user_id="user-123", notification_type=NotificationType.TASK_COMPLETED, channel=NotificationChannel.SMS, enabled=True),
        NotificationPreference(user_id="user-123", notification_type=NotificationType.TASK_COMPLETED, channel=NotificationChannel.PUSH, enabled=True),
    ])
    mock_preference_repo.execute = AsyncMock(return_value=mock_pref_result)

    # Side effects to return mock repos
    def get_repo_side_effect(model, session):
        if model == Notification:
            return mock_notification_repo
        elif model == NotificationRecipient:
            return mock_recipient_repo
        elif model == NotificationDelivery:
            return mock_delivery_repo
        elif model == NotificationPreference:
            return mock_preference_repo
        return MagicMock()

    with patch("app.features.notifications.tasks.Repository", side_effect=get_repo_side_effect):
        # Act
        await _process_batch("notif-123", ["recip-1"])

        # Assert
        # Check bulk_add gets called with deliveries. Only email and push should be present, SMS should be filtered out
        assert mock_delivery_repo.bulk_add.call_count == 1
        deliveries = mock_delivery_repo.bulk_add.call_args[0][0]
        
        channels_created = [d.channel for d in deliveries]
        assert NotificationChannel.EMAIL in channels_created
        assert NotificationChannel.PUSH in channels_created
        assert NotificationChannel.SMS not in channels_created  # Filtered out by notification.channels

        mock_email_delay.assert_called_once()
        mock_push_delay.assert_called_once()
        mock_sms_delay.assert_not_called()


@pytest.mark.asyncio
async def test_get_notification_counts(
    notification_service,
    mock_recipient_repo,
):
    # Arrange
    mock_result_unread = MagicMock()
    mock_result_unread.one = MagicMock(return_value=5)
    mock_result_read = MagicMock()
    mock_result_read.one = MagicMock(return_value=10)

    mock_recipient_repo.execute.side_effect = [mock_result_unread, mock_result_read]

    # Act
    counts = await notification_service.get_notification_counts(user_id="user-123")

    # Assert
    assert counts == {"read": 10, "unread": 5}
    assert mock_recipient_repo.execute.call_count == 2

