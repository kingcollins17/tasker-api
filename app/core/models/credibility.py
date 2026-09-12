import enum
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel

from app.core.utils.datetime_helper import lagos_now


class CredibilityReason(str, enum.Enum):
    """Categorises the event that caused a credibility score change."""
    # Positive events
    TASK_COMPLETED = "TASK_COMPLETED"
    FIVE_STAR_REVIEW = "FIVE_STAR_REVIEW"
    FOUR_STAR_REVIEW = "FOUR_STAR_REVIEW"
    ACCOUNT_VERIFIED = "ACCOUNT_VERIFIED"
    PROFILE_COMPLETED = "PROFILE_COMPLETED"
    # Neutral / zero
    THREE_STAR_REVIEW = "THREE_STAR_REVIEW"
    # Negative events
    TWO_STAR_REVIEW = "TWO_STAR_REVIEW"
    ONE_STAR_REVIEW = "ONE_STAR_REVIEW"
    TASK_DECLINED = "TASK_DECLINED"
    TASK_TIMEOUT = "TASK_TIMEOUT"
    THREE_CONSECUTIVE_DECLINES = "THREE_CONSECUTIVE_DECLINES"
    TASK_CANCELLED_BY_PROVIDER = "TASK_CANCELLED_BY_PROVIDER"
    TASK_CANCELLED_BY_CUSTOMER = "TASK_CANCELLED_BY_CUSTOMER"


# Fixed delta values per reason
CREDIBILITY_DELTAS: dict[str, float] = {
    CredibilityReason.TASK_COMPLETED: 3.0,
    CredibilityReason.FIVE_STAR_REVIEW: 5.0,
    CredibilityReason.FOUR_STAR_REVIEW: 2.0,
    CredibilityReason.THREE_STAR_REVIEW: 0.0,
    CredibilityReason.TWO_STAR_REVIEW: -2.0,
    CredibilityReason.ONE_STAR_REVIEW: -5.0,
    CredibilityReason.ACCOUNT_VERIFIED: 5.0,
    CredibilityReason.PROFILE_COMPLETED: 2.0,
    CredibilityReason.TASK_DECLINED: -1.0,
    CredibilityReason.TASK_TIMEOUT: -1.5,
    CredibilityReason.THREE_CONSECUTIVE_DECLINES: -5.0,
    CredibilityReason.TASK_CANCELLED_BY_PROVIDER: -3.0,
    CredibilityReason.TASK_CANCELLED_BY_CUSTOMER: -1.0,
}


def get_review_credibility_reason(rating: int) -> CredibilityReason:
    """Returns the CredibilityReason matching a star rating integer (1–5)."""
    mapping = {
        5: CredibilityReason.FIVE_STAR_REVIEW,
        4: CredibilityReason.FOUR_STAR_REVIEW,
        3: CredibilityReason.THREE_STAR_REVIEW,
        2: CredibilityReason.TWO_STAR_REVIEW,
        1: CredibilityReason.ONE_STAR_REVIEW,
    }
    return mapping.get(rating, CredibilityReason.THREE_STAR_REVIEW)


class CredibilityLedgerEntry(SQLModel, table=True):
    """Append-only ledger recording every credibility score change event for a user."""
    __tablename__ = "credibility_ledger"  # type: ignore

    id: str = Field(
        default_factory=lambda: str(uuid4()),
        primary_key=True,
        description="Unique ledger entry ID",
    )
    user_id: str = Field(
        foreign_key="users.id",
        index=True,
        ondelete="CASCADE",
        description="User whose credibility changed",
    )
    delta: float = Field(
        description="Credibility score change — positive for rewards, negative for penalties",
    )
    reason: CredibilityReason = Field(
        index=True,
        description="Categorised event type that caused this credibility change",
    )
    task_id: Optional[str] = Field(
        default=None,
        foreign_key="tasks.id",
        nullable=True,
        index=True,
        description="Associated task ID if this event is task-related",
    )
    metadata_info: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=Column(JSON),
        description="Optional JSON context for this ledger event",
    )
    created_at: datetime = Field(
        default_factory=lagos_now,
        description="Timestamp when this credibility event was recorded",
    )
