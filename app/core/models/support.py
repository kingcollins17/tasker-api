import enum
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import Column, JSON
from sqlmodel import Field, Relationship, SQLModel

from app.core.utils.datetime_helper import lagos_now


def generate_uuid_str() -> str:
    return str(uuid4())


class CaseType(str, enum.Enum):
    GENERAL = "GENERAL"
    DISPUTE = "DISPUTE"
    PAYMENT = "PAYMENT"
    TASK_ISSUE = "TASK_ISSUE"
    ACCOUNT = "ACCOUNT"
    TECHNICAL = "TECHNICAL"
    OTHER = "OTHER"


class CaseStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    WAITING_FOR_INTERNAL = "WAITING_FOR_INTERNAL"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class CasePriority(str, enum.Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class MessageSenderType(str, enum.Enum):
    CUSTOMER = "CUSTOMER"
    PROVIDER = "PROVIDER"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class MessageChannel(str, enum.Enum):
    EMAIL = "EMAIL"
    IN_APP = "IN_APP"


class MessageVisibility(str, enum.Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"


class CaseEventType(str, enum.Enum):
    CASE_CREATED = "CASE_CREATED"
    CASE_ASSIGNED = "CASE_ASSIGNED"
    CASE_REASSIGNED = "CASE_REASSIGNED"
    STATUS_CHANGED = "STATUS_CHANGED"
    PRIORITY_CHANGED = "PRIORITY_CHANGED"
    MESSAGE_SENT = "MESSAGE_SENT"
    MESSAGE_RECEIVED = "MESSAGE_RECEIVED"
    INTERNAL_NOTE_ADDED = "INTERNAL_NOTE_ADDED"
    ATTACHMENT_ADDED = "ATTACHMENT_ADDED"
    DISPUTE_OPENED = "DISPUTE_OPENED"
    DISPUTE_ESCALATED = "DISPUTE_ESCALATED"
    CASE_RESOLVED = "CASE_RESOLVED"
    CASE_REOPENED = "CASE_REOPENED"
    CASE_CLOSED = "CASE_CLOSED"


class SupportCase(SQLModel, table=True):
    """Core entity for customer support tickets and disputes."""
    id: str = Field(default_factory=generate_uuid_str, primary_key=True)
    case_number: str = Field(index=True, unique=True)
    type: CaseType = Field(default=CaseType.GENERAL, index=True)
    status: CaseStatus = Field(default=CaseStatus.OPEN, index=True)
    priority: CasePriority = Field(default=CasePriority.NORMAL, index=True)

    customer_id: Optional[str] = Field(default=None, index=True, nullable=True)
    provider_id: Optional[str] = Field(default=None, index=True, nullable=True)
    task_id: Optional[str] = Field(default=None, index=True, nullable=True)
    booking_id: Optional[str] = Field(default=None, nullable=True)
    payment_id: Optional[str] = Field(default=None, nullable=True)

    subject: str = Field(index=True)
    description: str
    assigned_agent_id: Optional[str] = Field(default=None, index=True, nullable=True)
    reply_token: Optional[str] = Field(default=None, index=True, nullable=True)

    first_response_due_at: Optional[datetime] = Field(default=None, nullable=True)
    resolution_due_at: Optional[datetime] = Field(default=None, nullable=True)
    first_responded_at: Optional[datetime] = Field(default=None, nullable=True)
    resolved_at: Optional[datetime] = Field(default=None, nullable=True)
    closed_at: Optional[datetime] = Field(default=None, nullable=True)

    created_at: datetime = Field(default_factory=lagos_now, index=True)
    updated_at: datetime = Field(default_factory=lagos_now, index=True)


class Dispute(SQLModel, table=True):
    """Dispute entity linked to a SupportCase."""
    id: str = Field(default_factory=generate_uuid_str, primary_key=True)
    case_id: str = Field(foreign_key="supportcase.id", index=True)
    task_id: Optional[str] = Field(default=None, index=True, nullable=True)
    booking_id: Optional[str] = Field(default=None, nullable=True)

    initiated_by: str = Field(index=True)
    reason: str
    amount_disputed: Optional[float] = Field(default=None, nullable=True)
    currency: str = Field(default="NGN")
    requested_resolution: Optional[str] = Field(default=None, nullable=True)

    created_at: datetime = Field(default_factory=lagos_now)


class CaseMessage(SQLModel, table=True):
    """Represents public communications or internal agent notes on a case."""
    id: str = Field(default_factory=generate_uuid_str, primary_key=True)
    case_id: str = Field(foreign_key="supportcase.id", index=True)

    sender_type: MessageSenderType = Field(index=True)
    sender_id: str = Field(index=True)

    channel: MessageChannel = Field(default=MessageChannel.IN_APP)
    visibility: MessageVisibility = Field(default=MessageVisibility.PUBLIC, index=True)

    body: str
    email_message_id: Optional[str] = Field(default=None, index=True, nullable=True)
    created_at: datetime = Field(default_factory=lagos_now, index=True)


class CaseEvent(SQLModel, table=True):
    """Append-only audit and timeline record for a support case."""
    id: str = Field(default_factory=generate_uuid_str, primary_key=True)
    case_id: str = Field(foreign_key="supportcase.id", index=True)

    event_type: CaseEventType = Field(index=True)
    actor_type: str
    actor_id: Optional[str] = Field(default=None, nullable=True)

    event_metadata: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON, nullable=True))
    created_at: datetime = Field(default_factory=lagos_now, index=True)


class CaseAssignment(SQLModel, table=True):
    """Agent assignment history record."""
    id: str = Field(default_factory=generate_uuid_str, primary_key=True)
    case_id: str = Field(foreign_key="supportcase.id", index=True)
    agent_id: str = Field(index=True)
    assigned_by: str
    assigned_at: datetime = Field(default_factory=lagos_now)
    unassigned_at: Optional[datetime] = Field(default=None, nullable=True)
    reason: Optional[str] = Field(default=None, nullable=True)


class CaseAttachment(SQLModel, table=True):
    """File attachment linked to a case and optional message."""
    id: str = Field(default_factory=generate_uuid_str, primary_key=True)
    case_id: str = Field(foreign_key="supportcase.id", index=True)
    message_id: Optional[str] = Field(default=None, foreign_key="casemessage.id", index=True, nullable=True)

    uploaded_by: str = Field(index=True)
    storage_key: str
    filename: str
    mime_type: str
    size: int
    created_at: datetime = Field(default_factory=lagos_now)


class CaseResolution(SQLModel, table=True):
    """Final resolution record for a case."""
    id: str = Field(default_factory=generate_uuid_str, primary_key=True)
    case_id: str = Field(foreign_key="supportcase.id", index=True)

    decision: str
    reason: str
    resolved_by: str = Field(index=True)
    created_at: datetime = Field(default_factory=lagos_now)
