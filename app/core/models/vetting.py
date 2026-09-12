from typing import Optional
from datetime import datetime
from uuid import uuid4
from sqlmodel import Field, SQLModel
from sqlalchemy import Column, JSON
from app.core.utils.datetime_helper import lagos_now
from .users import VerificationStatus

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
    verified_at: Optional[datetime] = Field(default=None, description="Timestamp when the guarantor verified")
    created_at: datetime = Field(default_factory=lagos_now, description="Record creation timestamp")
