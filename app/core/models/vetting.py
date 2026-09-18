import enum
from typing import Optional
from datetime import datetime
from uuid import uuid4
from sqlmodel import Field, SQLModel
from sqlalchemy import Column, JSON
from app.core.utils.datetime_helper import lagos_now
from .users import VerificationStatus


class InterviewStatus(str, enum.Enum):
    """Status for provider interview sessions."""
    SCHEDULED = "SCHEDULED"
    PASSED = "PASSED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RESCHEDULED = "RESCHEDULED"


class ProviderGuarantor(SQLModel, table=True):
    """Guarantor and identity verification."""
    __tablename__ = "provider_guarantors"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique identifier for guarantor entry")
    provider_id: str = Field(foreign_key="users.id", ondelete="CASCADE", description="Foreign key reference to provider user ID")
    guarantor_name: str = Field(nullable=False, max_length=150, description="Full name of the guarantor")
    guarantor_phone: str = Field(nullable=False, max_length=20, description="Phone number of the guarantor")
    relationship: Optional[str] = Field(default=None, max_length=50, description="Relationship to the provider (e.g. Trade Master)")
    token_hash: str = Field(unique=True, nullable=False, max_length=255, description="One-time verification link token")
    status: VerificationStatus = Field(default=VerificationStatus.PENDING, description="Verification status of the guarantor attestation")
    meta_data: dict = Field(default_factory=dict, sa_column=Column(JSON), description="Provider-specific JSON metadata payload")
    
    verified_at: Optional[datetime] = Field(default=None, description="Timestamp when the guarantor verified")
    created_at: datetime = Field(default_factory=lagos_now, description="Record creation timestamp")


class ProviderInterview(SQLModel, table=True):
    """Stores scheduled online interviews for providers during vetting."""
    __tablename__ = "provider_interviews"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique primary identifier for interview record")
    user_id: str = Field(foreign_key="users.id", index=True, ondelete="CASCADE", description="Foreign key reference to provider user ID")
    admin_id: Optional[str] = Field(default=None, foreign_key="admins.id", index=True, ondelete="SET NULL", description="Foreign key reference to scheduling admin user")
    scheduled_at: datetime = Field(nullable=False, description="Scheduled date and time for the online interview")
    meeting_link: Optional[str] = Field(default=None, description="Online meeting URL (e.g. Google Meet, Zoom)")
    status: InterviewStatus = Field(default=InterviewStatus.SCHEDULED, description="Current status of the interview")
    notes: Optional[str] = Field(default=None, description="Admin review feedback or notes")
    passed_at: Optional[datetime] = Field(default=None, description="Timestamp when user passed the interview")
    created_at: datetime = Field(default_factory=lagos_now, description="Record creation timestamp")
    updated_at: datetime = Field(default_factory=lagos_now, description="Record last update timestamp")
    meta_data: dict = Field(default_factory=dict, sa_column=Column(JSON), description="JSON metadata payload")
