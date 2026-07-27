import enum
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy import Column, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel, Relationship

from app.core.utils.datetime_helper import lagos_now


class TaskReview(SQLModel, table=True):
    """Review submitted by a customer or provider after a task is completed."""

    __tablename__ = "task_reviews"  # type: ignore
    __table_args__ = (
        UniqueConstraint("task_id", "reviewer_id", name="uq_task_review_reviewer"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()),
        primary_key=True,
        description="Unique review ID",
    )
    task_id: str = Field(
        foreign_key="tasks.id",
        index=True,
        ondelete="CASCADE",
        description="Task being reviewed",
    )
    reviewer_id: str = Field(
        foreign_key="users.id",
        index=True,
        ondelete="CASCADE",
        description="User who submitted this review",
    )
    reviewee_id: str = Field(
        foreign_key="users.id",
        index=True,
        ondelete="CASCADE",
        description="User being reviewed",
    )
    rating: int = Field(
        ge=1,
        le=5,
        description="Star rating from 1 (worst) to 5 (best)",
    )
    comment: Optional[str] = Field(
        default=None,
        nullable=True,
        description="Optional free-text review comment",
    )
    is_visible: bool = Field(
        default=False,
        description="Hidden until double-blind window expires or both parties have reviewed",
    )
    created_at: datetime = Field(
        default_factory=lagos_now, description="Review submission timestamp"
    )
    updated_at: datetime = Field(
        default_factory=lagos_now, description="Record update timestamp"
    )
