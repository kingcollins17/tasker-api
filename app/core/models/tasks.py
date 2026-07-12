import enum
from datetime import datetime
from uuid import uuid4
from typing import List, Optional, Any
from sqlmodel import Field, SQLModel, Relationship
from sqlalchemy import Column
from app.core.utils.datetime_helper import utc_now
from app.core.models.spatial import PointType
from sqlalchemy.orm import query_expression


class LocationType(str, enum.Enum):
    PICKUP = "pickup"
    DROPOFF = "dropoff"
    SERVICE = "service"


class TaskStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    MATCHING = "matching"
    BIDDING = "bidding"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class TaskBidStatus(str, enum.Enum):
    PENDING = "pending"
    WITHDRAWN = "withdrawn"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    EXPIRED = "expired"


class TaskAssignmentStatus(str, enum.Enum):
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Task(SQLModel, table=True):
    __tablename__ = "tasks"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    customer_id: Optional[str] = Field(default=None, foreign_key="users.id", index=True, ondelete="SET NULL", nullable=True)
    region_id: Optional[str] = Field(
        default=None, foreign_key="regions.id", index=True, nullable=True
    )
    title: str = Field(nullable=False)
    description: str = Field(nullable=False)
    category_id: Optional[str] = Field(
        default=None, foreign_key="categories.id", index=True, nullable=True
    )
    service_id: Optional[str] = Field(
        default=None, foreign_key="services.id", index=True, nullable=True
    )
    budget_min: Optional[float] = Field(default=None, nullable=True)
    budget_max: Optional[float] = Field(default=None, nullable=True)
    pricing_model: str = Field(default="fixed")
    status: TaskStatus = Field(default=TaskStatus.OPEN, index=True)
    scheduled_start_at: Optional[datetime] = Field(default=None, nullable=True)
    start_pin: Optional[str] = Field(default=None, nullable=True)
    completion_pin: Optional[str] = Field(default=None, nullable=True)
    expires_at: Optional[datetime] = Field(default=None, index=True, nullable=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)

    # Relationships
    locations: List["TaskLocation"] = Relationship(
        back_populates="task",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "lazy": "joined",
        },
    )
    bids: List["TaskBid"] = Relationship(
        back_populates="task",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )
    assignment: Optional["TaskAssignment"] = Relationship(
        back_populates="task",
        sa_relationship_kwargs={
            "uselist": False,
            "cascade": "all, delete-orphan",
            "lazy": "joined",
        },
    )
    history: List["TaskStatusHistory"] = Relationship(
        back_populates="task",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )
    attachments: List["TaskAttachment"] = Relationship(
        back_populates="task",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )


class TaskLocation(SQLModel, table=True):
    __tablename__ = "task_locations"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    task_id: str = Field(foreign_key="tasks.id", index=True, ondelete="CASCADE")
    location_type: LocationType = Field(default=LocationType.SERVICE, index=True)
    latitude: float
    longitude: float
    address: Optional[str] = Field(default=None, nullable=True)
    city: Optional[str] = Field(default=None, nullable=True)
    state: Optional[str] = Field(default=None, nullable=True)
    country: Optional[str] = Field(default=None, nullable=True)
    geography_point: Optional[Any] = Field(
        default=None, sa_column=Column(PointType, nullable=True)
    )
    distance_km: Optional[float] = Field(default=None, sa_column=query_expression())  # type: ignore
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    task: Task = Relationship(back_populates="locations")


class TaskBid(SQLModel, table=True):
    __tablename__ = "task_bids"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    task_id: str = Field(foreign_key="tasks.id", index=True, ondelete="CASCADE")
    provider_id: str = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    price: float
    message: Optional[str] = Field(default=None, nullable=True)
    estimated_duration: Optional[str] = Field(default=None, nullable=True)
    status: TaskBidStatus = Field(default=TaskBidStatus.PENDING, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)

    task: Task = Relationship(back_populates="bids")


class TaskAssignment(SQLModel, table=True):
    __tablename__ = "task_assignments"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    task_id: str = Field(foreign_key="tasks.id", unique=True, index=True, ondelete="CASCADE")
    provider_id: str = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    accepted_bid_id: Optional[str] = Field(
        default=None, foreign_key="task_bids.id", nullable=True
    )
    accepted_price: float
    assigned_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = Field(default=None, nullable=True)
    completed_at: Optional[datetime] = Field(default=None, nullable=True)
    status: TaskAssignmentStatus = Field(default=TaskAssignmentStatus.ASSIGNED)

    task: Task = Relationship(back_populates="assignment")


class TaskStatusHistory(SQLModel, table=True):
    __tablename__ = "task_status_history"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    task_id: str = Field(foreign_key="tasks.id", index=True, ondelete="CASCADE")
    old_status: Optional[TaskStatus] = Field(default=None, nullable=True)
    new_status: TaskStatus
    changed_by: Optional[str] = Field(
        default=None, foreign_key="users.id", nullable=True
    )
    timestamp: datetime = Field(default_factory=utc_now)

    task: Task = Relationship(back_populates="history")


class TaskAttachment(SQLModel, table=True):
    __tablename__ = "task_attachments"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    task_id: str = Field(foreign_key="tasks.id", index=True, ondelete="CASCADE", description="The ID of the task this attachment is associated with")
    storage_key: str = Field(description="The unique object key/path inside the cloud storage system (e.g. 'tasks/task-uuid/image.png')")
    file_name: Optional[str] = Field(default=None, nullable=True, description="The original name of the uploaded file")
    file_size: Optional[int] = Field(default=None, nullable=True, description="The size of the file in bytes")
    mime_type: Optional[str] = Field(default=None, nullable=True, description="The standard internet media type of the file (e.g. 'image/jpeg', 'video/mp4')")
    url: Optional[str] = Field(default=None, nullable=True, description="The public-facing URL to access/download the file")
    type: Optional[str] = Field(default=None, nullable=True, description="Semantic workflow category of the file (e.g. 'before_photo', 'after_photo', 'invoice')")
    created_at: datetime = Field(default_factory=utc_now)

    task: Task = Relationship(back_populates="attachments")

