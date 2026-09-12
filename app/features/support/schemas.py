from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.core.models.support import (
    CaseType,
    CaseStatus,
    CasePriority,
    MessageSenderType,
    MessageChannel,
    MessageVisibility,
    CaseEventType,
)


class SupportCaseCreate(BaseModel):
    subject: str = Field(..., min_length=3, max_length=255, description="Brief summary of issue")
    description: str = Field(..., min_length=5, description="Detailed explanation of support request")
    type: CaseType = Field(default=CaseType.GENERAL, description="Support ticket category")
    priority: CasePriority = Field(default=CasePriority.NORMAL, description="Ticket priority level")
    task_id: Optional[str] = Field(default=None, description="Optional associated task ID")
    booking_id: Optional[str] = Field(default=None, description="Optional associated booking ID")
    payment_id: Optional[str] = Field(default=None, description="Optional associated payment transaction ID")


class SupportCaseUpdate(BaseModel):
    status: Optional[CaseStatus] = None
    priority: Optional[CasePriority] = None
    subject: Optional[str] = None
    description: Optional[str] = None


class SupportCaseResponse(BaseModel):
    id: str
    case_number: str
    type: CaseType
    status: CaseStatus
    priority: CasePriority
    customer_id: Optional[str] = None
    provider_id: Optional[str] = None
    task_id: Optional[str] = None
    booking_id: Optional[str] = None
    payment_id: Optional[str] = None
    subject: str
    description: str
    assigned_agent_id: Optional[str] = None
    reply_token: Optional[str] = None
    first_response_due_at: Optional[datetime] = None
    resolution_due_at: Optional[datetime] = None
    first_responded_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class CaseMessageCreate(BaseModel):
    body: str = Field(..., min_length=1, description="Message body content")
    channel: MessageChannel = Field(default=MessageChannel.IN_APP, description="Communication channel")


class InternalNoteCreate(BaseModel):
    body: str = Field(..., min_length=1, description="Internal agent note content")


class CaseMessageResponse(BaseModel):
    id: str
    case_id: str
    sender_type: MessageSenderType
    sender_id: str
    channel: MessageChannel
    visibility: MessageVisibility
    body: str
    email_message_id: Optional[str] = None
    created_at: datetime


class DisputeCreate(BaseModel):
    task_id: str = Field(..., description="ID of disputed task")
    reason: str = Field(..., min_length=5, description="Detailed dispute explanation")
    amount_disputed: Optional[float] = Field(default=None, ge=0.0, description="Optional disputed monetary amount")
    requested_resolution: Optional[str] = Field(default=None, description="Desired outcome requested by user")
    booking_id: Optional[str] = None


class DisputeResponse(BaseModel):
    id: str
    case_id: str
    task_id: Optional[str] = None
    booking_id: Optional[str] = None
    initiated_by: str
    reason: str
    amount_disputed: Optional[float] = None
    currency: str
    requested_resolution: Optional[str] = None
    created_at: datetime


class CaseAssignmentCreate(BaseModel):
    agent_id: str = Field(..., description="ID of agent to assign")
    reason: Optional[str] = Field(default=None, description="Reason for assignment or transfer")


class CaseResolutionCreate(BaseModel):
    decision: str = Field(..., min_length=3, description="Final resolution decision summary")
    reason: str = Field(..., min_length=5, description="Detailed rationale for decision")


class CaseAttachmentResponse(BaseModel):
    id: str
    case_id: str
    message_id: Optional[str] = None
    uploaded_by: str
    storage_key: str
    filename: str
    mime_type: str
    size: int
    created_at: datetime


class TimelineItemResponse(BaseModel):
    id: str
    item_type: str  # "EVENT" or "MESSAGE"
    timestamp: datetime
    title: str
    description: Optional[str] = None
    actor_type: Optional[str] = None
    actor_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
