import enum
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import Column, Enum as SQLEnum, Index, JSON, text
from sqlmodel import Field, Relationship, SQLModel

from app.core.utils.datetime_helper import lagos_now


# ── Enums ────────────────────────────────────────────────────────────────────

class NotificationType(str, enum.Enum):
    TASK_ACCEPTED = "TASK_ACCEPTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_CANCELLED = "TASK_CANCELLED"
    PAYMENT_REQUESTED = "PAYMENT_REQUESTED"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    NEW_MESSAGE = "NEW_MESSAGE"
    REVIEW_RECEIVED = "REVIEW_RECEIVED"
    PROMOTION = "PROMOTION"
    SECURITY_ALERT = "SECURITY_ALERT"
    SYSTEM_ALERT = "SYSTEM_ALERT"
    JOB_PING = "JOB_PING"


class NotificationChannel(str, enum.Enum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    PUSH = "PUSH"
    WHATSAPP = "WHATSAPP"
    IN_APP = "IN_APP"


class NotificationPriority(str, enum.Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RecipientStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    READ = "READ"


class DeliveryStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


# ── Tables ───────────────────────────────────────────────────────────────────

class Notification(SQLModel, table=True):
    """Represents a single notification event, independent of recipients."""
    __tablename__ = "notifications"  # type: ignore
    __table_args__ = (
        Index("ix_notifications_type", "type"),
        Index("ix_notifications_created_at", "created_at"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    type: Optional[NotificationType] = Field(
        default=None,
        sa_column=Column(
            SQLEnum(NotificationType, name="notificationtype", values_callable=lambda x: [e.value for e in x]),
            nullable=True,
        ),
        description="Type of notification.",
    )
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
    priority: NotificationPriority = Field(
        default=NotificationPriority.NORMAL,
        sa_column=Column(
            SQLEnum(NotificationPriority, name="notificationpriority", values_callable=lambda x: [e.value for e in x]),
            nullable=False,
            default=NotificationPriority.NORMAL,
        ),
    )
    created_by: Optional[str] = Field(
        default=None, foreign_key="users.id", index=True
    )
    created_at: datetime = Field(default_factory=lagos_now)
    scheduled_for: Optional[datetime] = Field(default=None)
    expires_at: Optional[datetime] = Field(default=None)

    recipients: List["NotificationRecipient"] = Relationship(
        back_populates="notification",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )


class NotificationRecipient(SQLModel, table=True):
    """Maps a notification to a specific user and tracks their feed read state."""
    __tablename__ = "notification_recipients"  # type: ignore
    __table_args__ = (
        Index("ix_notif_recip_recipient_created", "recipient_id", "created_at"),
        Index("ix_notif_recip_notification", "notification_id"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    notification_id: str = Field(foreign_key="notifications.id", index=True, ondelete="CASCADE")
    recipient_id: str = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    status: RecipientStatus = Field(
        default=RecipientStatus.PENDING,
        sa_column=Column(
            SQLEnum(RecipientStatus, name="recipientstatus", values_callable=lambda x: [e.value for e in x]),
            nullable=False,
            default=RecipientStatus.PENDING,
        ),
    )
    read_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=lagos_now)

    notification: Notification = Relationship(back_populates="recipients")
    deliveries: List["NotificationDelivery"] = Relationship(
        back_populates="recipient",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )


class NotificationDelivery(SQLModel, table=True):
    """Tracks each delivery attempt per channel (email, sms, push, whatsapp) for a recipient."""
    __tablename__ = "notification_delivery"  # type: ignore
    __table_args__ = (
        Index("ix_notif_deliv_status_channel", "status", "channel"),
        Index("ix_notif_deliv_recipient", "recipient_id"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    recipient_id: str = Field(foreign_key="notification_recipients.id", index=True, ondelete="CASCADE")
    channel: NotificationChannel = Field(
        sa_column=Column(
            SQLEnum(NotificationChannel, name="notificationchannel", values_callable=lambda x: [e.value for e in x]),
            nullable=False,
        )
    )
    status: DeliveryStatus = Field(
        default=DeliveryStatus.PENDING,
        sa_column=Column(
            SQLEnum(DeliveryStatus, name="deliverystatus", values_callable=lambda x: [e.value for e in x]),
            nullable=False,
            default=DeliveryStatus.PENDING,
        ),
    )
    attempt: int = Field(default=1)
    provider_message_id: Optional[str] = Field(default=None)
    sent_at: Optional[datetime] = Field(default=None)
    delivered_at: Optional[datetime] = Field(default=None)
    failure_reason: Optional[str] = Field(default=None)

    recipient: NotificationRecipient = Relationship(back_populates="deliveries")



