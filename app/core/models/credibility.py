import enum
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel

from app.core.utils.datetime_helper import utc_now


class CredibilityReason(str, enum.Enum):
    """Categorises the event that caused a credibility score change."""
    # Positive events
    TASK_COMPLETED = "task_completed"
    FIVE_STAR_REVIEW = "five_star_review"
    FOUR_STAR_REVIEW = "four_star_review"
    ACCOUNT_VERIFIED = "account_verified"
    PROFILE_COMPLETED = "profile_completed"
    # Neutral / zero
    THREE_STAR_REVIEW = "three_star_review"
    # Negative events
    TWO_STAR_REVIEW = "two_star_review"
    ONE_STAR_REVIEW = "one_star_review"
    JOB_DECLINED = "job_declined"
    JOB_TIMEOUT = "job_timeout"
    THREE_CONSECUTIVE_DECLINES = "three_consecutive_declines"
    TASK_CANCELLED_BY_PROVIDER = "task_cancelled_by_provider"
    TASK_CANCELLED_BY_CUSTOMER = "task_cancelled_by_customer"


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
    CredibilityReason.JOB_DECLINED: -1.0,
    CredibilityReason.JOB_TIMEOUT: -1.5,
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
        default_factory=utc_now,
        description="Timestamp when this credibility event was recorded",
    )
