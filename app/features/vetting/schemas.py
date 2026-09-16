from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from app.core.models.users import VerificationStatus
from app.core.models.vetting import InterviewStatus


# Guarantor Schemas
class AddGuarantorRequest(BaseModel):
    guarantor_name: str = Field(..., description="Full name of the guarantor")
    guarantor_phone: str = Field(..., description="Phone number of the guarantor")
    relationship: Optional[str] = Field(None, description="Relationship to the provider")


class ResubmitGuarantorRequest(BaseModel):
    guarantor_name: str = Field(..., description="Full name of the new or updated guarantor")
    guarantor_phone: str = Field(..., description="Phone number of the new or updated guarantor")
    relationship: Optional[str] = Field(None, description="Relationship to the provider")


class ApproveGuarantorRequest(BaseModel):
    notes: Optional[str] = Field(None, description="Optional admin review notes for approval")


class RejectGuarantorRequest(BaseModel):
    reason: str = Field(..., description="Reason for rejecting the guarantor verification")
    notes: Optional[str] = Field(None, description="Optional admin review notes")


class GuarantorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider_id: str
    guarantor_name: str
    guarantor_phone: str
    relationship: Optional[str] = None
    status: VerificationStatus
    meta_data: Dict[str, Any] = Field(default_factory=dict)
    verified_at: Optional[datetime] = None
    created_at: datetime


class AddGuarantorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None


# Interview Schemas
class ScheduleInterviewRequest(BaseModel):
    user_id: str = Field(..., description="ID of the provider user to schedule interview for")
    scheduled_at: datetime = Field(..., description="Date and time for the online interview")
    meeting_link: Optional[str] = Field(None, description="Online meeting URL (e.g., Google Meet link)")
    notes: Optional[str] = Field(None, description="Optional admin notes for the interview")


class UpdateInterviewStatusRequest(BaseModel):
    status: InterviewStatus = Field(..., description="Updated status of the interview (PASSED, FAILED, CANCELLED, RESCHEDULED)")
    notes: Optional[str] = Field(None, description="Optional admin notes or review comments")
    meeting_link: Optional[str] = Field(None, description="Updated meeting link if rescheduled")
    scheduled_at: Optional[datetime] = Field(None, description="Updated schedule time if rescheduled")


class InterviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    admin_id: Optional[str] = None
    scheduled_at: datetime
    meeting_link: Optional[str] = None
    status: InterviewStatus
    notes: Optional[str] = None
    passed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    meta_data: Dict[str, Any] = Field(default_factory=dict)


# KYC Admin Review Schemas
class ApproveKYCRequest(BaseModel):
    notes: Optional[str] = Field(None, description="Optional admin review notes for KYC approval")


class RejectKYCRequest(BaseModel):
    reason: str = Field(..., description="Reason for rejecting the KYC document verification")
    notes: Optional[str] = Field(None, description="Optional admin review notes")


class KYCDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    id_type: str
    id_number: str
    id_doc_url: str
    status: str
    rejection_reason: Optional[str] = None
    attempt_number: int
    submitted_at: datetime
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
