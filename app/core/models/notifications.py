import enum
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import Column, Index, JSON, text
from sqlmodel import Field, Relationship, SQLModel

from app.core.utils.datetime_helper import utc_now


# ── Enums ────────────────────────────────────────────────────────────────────

class NotificationType(str, enum.Enum):
    TASK_ACCEPTED = "task_accepted"
    TASK_COMPLETED = "task_completed"
    TASK_CANCELLED = "task_cancelled"
    PAYMENT_RECEIVED = "payment_received"
    PAYMENT_FAILED = "payment_failed"
    NEW_MESSAGE = "new_message"
    REVIEW_RECEIVED = "review_received"
    PROMOTION = "promotion"
    SECURITY_ALERT = "security_alert"


class NotificationChannel(str, enum.Enum):
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"
    WHATSAPP = "whatsapp"
    IN_APP = "in_app"


class NotificationPriority(str, enum.Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class RecipientStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    READ = "read"


class DeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    PERMANENT_FAILURE = "permanent_failure"


# ── Tables ───────────────────────────────────────────────────────────────────

class Notification(SQLModel, table=True):
    """Represents a single notification event, independent of recipients."""
    __tablename__ = "notifications"  # type: ignore
    __table_args__ = (
        Index("ix_notifications_type", "type"),
        Index("ix_notifications_created_at", "created_at"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    type: NotificationType
    title: str = Field(max_length=255)
    body: str
    data: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    channels: Optional[List[str]] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Allowed delivery channels. None means all channels.",
    )
    priority: NotificationPriority = Field(default=NotificationPriority.NORMAL)
    created_by: Optional[str] = Field(
        default=None, foreign_key="users.id", index=True
    )
    created_at: datetime = Field(default_factory=utc_now)
    scheduled_for: Optional[datetime] = Field(default=None)
    expires_at: Optional[datetime] = Field(default=None)

    recipients: List["NotificationRecipient"] = Relationship(
        back_populates="notification",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )


class NotificationRecipient(SQLModel, table=True):
    """Maps a notification to a specific user and tracks their feed read state.
    
    Status Lifecycle:
        - PENDING: Notification is created but not yet fanned out.
        - SENT: Notification has been processed by the fan-out task and queued for
                the delivery channels. It is now active and visible in the user's
                in-app notification feed.
        - READ: The user viewed/read the notification.
    """
    __tablename__ = "notification_recipients"  # type: ignore
    __table_args__ = (
        Index("ix_notif_recip_recipient_created", "recipient_id", "created_at"),
        Index("ix_notif_recip_notification", "notification_id"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    notification_id: str = Field(foreign_key="notifications.id", index=True, ondelete="CASCADE")
    recipient_id: str = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    status: RecipientStatus = Field(default=RecipientStatus.PENDING)
    read_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)

    notification: Notification = Relationship(back_populates="recipients")
    deliveries: List["NotificationDelivery"] = Relationship(
        back_populates="recipient",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )


class NotificationDelivery(SQLModel, table=True):
    """Tracks each delivery attempt per channel (email, sms, push, whatsapp) for a recipient.
    
    Status Lifecycle:
        - PENDING: Delivery job is created and queued, waiting to be sent by provider.
        - DELIVERED: Provider successfully sent the message.
        - FAILED: Delivery attempt failed (might be retried).
        - PERMANENT_FAILURE: Delivery failed and cannot be retried (e.g. user missing device token).
    """
    __tablename__ = "notification_delivery"  # type: ignore
    __table_args__ = (
        Index("ix_notif_deliv_status_channel", "status", "channel"),
        Index("ix_notif_deliv_recipient", "recipient_id"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    recipient_id: str = Field(foreign_key="notification_recipients.id", index=True, ondelete="CASCADE")
    channel: NotificationChannel
    status: DeliveryStatus = Field(default=DeliveryStatus.PENDING)
    attempt: int = Field(default=1)
    provider_message_id: Optional[str] = Field(default=None)
    sent_at: Optional[datetime] = Field(default=None)
    delivered_at: Optional[datetime] = Field(default=None)
    failure_reason: Optional[str] = Field(default=None)

    recipient: NotificationRecipient = Relationship(back_populates="deliveries")


class NotificationPreference(SQLModel, table=True):
    """Per-user, per-type, per-channel preference toggle."""
    __tablename__ = "notification_preferences"  # type: ignore
    __table_args__ = (
        Index("ix_notif_pref_user_type", "user_id", "notification_type"),
        Index("ix_notif_pref_user_type_channel", "user_id", "notification_type", "channel", unique=True),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    notification_type: NotificationType
    channel: NotificationChannel
    enabled: bool = Field(default=True)
