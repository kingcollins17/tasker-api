from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.core.models.notifications import (
    DeliveryStatus,
    NotificationChannel,
    NotificationPriority,
    NotificationType,
    RecipientStatus,
)


# ── Request Schemas ──────────────────────────────────────────────────────────

class CreateNotification(BaseModel):
    """Input schema for creating a new notification and dispatching it."""
    type: NotificationType = Field(..., description="Category of the notification")
    title: str = Field(..., max_length=255, description="Short notification title")
    body: str = Field(..., description="Full notification body text")
    data: Optional[Dict[str, Any]] = Field(
        None, description="Arbitrary JSON metadata (booking_id, task_id, etc.)"
    )
    priority: NotificationPriority = Field(
        NotificationPriority.NORMAL, description="Delivery priority"
    )
    recipient_ids: List[str] = Field(
        ..., min_length=1, description="List of user IDs who should receive this"
    )
    channels: Optional[List[NotificationChannel]] = Field(
        None,
        description="Channels to deliver through (e.g. ['email', 'push']). None means all channels.",
    )
    scheduled_for: Optional[datetime] = Field(
        None, description="If set, delivery is deferred until this UTC time"
    )
    expires_at: Optional[datetime] = Field(
        None, description="If set, notification expires and is not delivered after this UTC time"
    )


class MarkReadRequest(BaseModel):
    """Input schema for marking notifications as read."""
    notification_ids: List[str] = Field(
        ..., min_length=1, description="List of notification IDs to mark as read"
    )


class UpdatePreference(BaseModel):
    """Single preference toggle."""
    notification_type: NotificationType
    channel: NotificationChannel
    enabled: bool


class BulkUpdatePreferences(BaseModel):
    """Bulk-update user notification preferences."""
    preferences: List[UpdatePreference] = Field(
        ..., min_length=1, description="List of preferences to upsert"
    )


# ── Response Schemas ─────────────────────────────────────────────────────────

class NotificationDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    recipient_id: Optional[str] = None
    channel: Optional[NotificationChannel] = None
    status: Optional[DeliveryStatus] = None
    attempt: Optional[int] = None
    provider_message_id: Optional[str] = None
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    failure_reason: Optional[str] = None


class NotificationRecipientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    notification_id: Optional[str] = None
    recipient_id: Optional[str] = None
    status: Optional[RecipientStatus] = None
    read_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    deliveries: Optional[List[NotificationDeliveryResponse]] = None


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    type: Optional[NotificationType] = None
    title: Optional[str] = None
    body: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    priority: Optional[NotificationPriority] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    scheduled_for: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    recipients: Optional[List[NotificationRecipientResponse]] = None


class UserNotificationResponse(BaseModel):
    """Flat view of a notification from the recipient's perspective."""
    model_config = ConfigDict(from_attributes=True)

    notification_id: Optional[str] = None
    recipient_record_id: Optional[str] = None
    type: Optional[NotificationType] = None
    title: Optional[str] = None
    body: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    priority: Optional[NotificationPriority] = None
    status: Optional[RecipientStatus] = None
    read_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class NotificationCountsResponse(BaseModel):
    read: Optional[int] = None
    unread: Optional[int] = None


class NotificationPreferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    user_id: Optional[str] = None
    notification_type: Optional[NotificationType] = None
    channel: Optional[NotificationChannel] = None
    enabled: Optional[bool] = None
