from typing import Optional
from pydantic import BaseModel, Field, field_validator


class CreateReviewRequest(BaseModel):
    """Payload for submitting a review after task completion."""
    task_id: str = Field(description="ID of the completed task being reviewed")
    rating: int = Field(ge=1, le=5, description="Star rating from 1 to 5")
    comment: Optional[str] = Field(default=None, description="Optional review text")

    @field_validator("rating")
    @classmethod
    def rating_in_range(cls, v: int) -> int:
        if not (1 <= v <= 5):
            raise ValueError("Rating must be between 1 and 5")
        return v


class ReviewResponse(BaseModel):
    """Public representation of a submitted review."""
    id: Optional[str] = None
    task_id: Optional[str] = None
    reviewer_id: Optional[str] = None
    reviewee_id: Optional[str] = None
    rating: Optional[int] = None
    comment: Optional[str] = None
    is_visible: Optional[bool] = None
    created_at: Optional[str] = None


class CredibilityLedgerEntryResponse(BaseModel):
    """Public representation of a single credibility ledger entry."""
    id: Optional[str] = None
    user_id: Optional[str] = None
    delta: Optional[float] = None
    reason: Optional[str] = None
    task_id: Optional[str] = None
    created_at: Optional[str] = None
