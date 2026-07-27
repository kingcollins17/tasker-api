from typing import Optional
from datetime import datetime
from uuid import uuid4
from sqlmodel import Field, SQLModel
from sqlalchemy import Column, JSON
from app.core.utils.datetime_helper import lagos_now
from .users import VerificationStatus, MediaType

class QuizQuestion(SQLModel, table=True):
    """Randomized quiz question bank for specific trades/services."""
    __tablename__ = "quiz_questions"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique identifier for quiz question")
    category_id: str = Field(foreign_key="categories.id", ondelete="CASCADE", description="Service category this question belongs to")
    question_text: str = Field(nullable=False, description="The actual question text")
    options: dict = Field(default_factory=dict, sa_column=Column(JSON), description="JSON object mapping options e.g. {'A': 'Option 1', 'B': 'Option 2'}")
    correct_option: str = Field(nullable=False, description="The correct option key e.g. 'B'")
    created_at: datetime = Field(default_factory=lagos_now, description="Record creation timestamp")

class ProviderQuizResult(SQLModel, table=True):
    """Quiz attempts and scoring for providers."""
    __tablename__ = "provider_quiz_results"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique identifier for quiz result")
    provider_id: str = Field(foreign_key="users.id", ondelete="CASCADE", description="Foreign key reference to provider user ID")
    category_id: str = Field(foreign_key="categories.id", ondelete="CASCADE", description="Foreign key reference to the service category tested")
    score_percentage: float = Field(nullable=False, description="Score achieved on the quiz")
    status: VerificationStatus = Field(nullable=False, description="Verification status, 'passed' if score is sufficient")
    attempted_at: datetime = Field(default_factory=lagos_now, description="Timestamp of the attempt")

class ProviderPortfolioMedia(SQLModel, table=True):
    """Media and equipment portfolio verification."""
    __tablename__ = "provider_portfolio_media"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique identifier for media entry")
    provider_id: str = Field(foreign_key="users.id", ondelete="CASCADE", description="Foreign key reference to provider user ID")
    category_id: Optional[str] = Field(default=None, foreign_key="categories.id", ondelete="SET NULL", description="Optional foreign key to specific service category")
    media_url: str = Field(nullable=False, description="URL to the uploaded media")
    media_type: MediaType = Field(nullable=False, description="Type of media uploaded")
    status: VerificationStatus = Field(default=VerificationStatus.PENDING, description="Verification status of the media")
    uploaded_at: datetime = Field(default_factory=lagos_now, description="Timestamp of the upload")

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
