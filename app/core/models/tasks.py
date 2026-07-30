from sqlalchemy import null
import enum
from datetime import datetime
from uuid import uuid4
from typing import List, Optional, Any
from sqlmodel import Field, SQLModel, Relationship
from sqlalchemy import Column
from app.core.utils.datetime_helper import lagos_now
from app.core.models.spatial import PointType
from sqlalchemy.orm import query_expression

class LocationType(str, enum.Enum):
    """Discriminates task geographical point roles."""
    PICKUP = "pickup"
    DROPOFF = "dropoff"
    SERVICE = "service"

class TaskStatus(str, enum.Enum):
    """Lifecycle states of a task request."""
    DRAFT = "draft"
    OPEN = "open"
    SEARCHING = "searching"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

class PaymentMode(str, enum.Enum):
    """Supported payment settlement modes."""
    CASH = "cash"
    ONLINE = "online"

class PaymentStatus(str, enum.Enum):
    """Lifecycle states of task payment settlement."""
    PENDING = "pending"
    PAYMENT_REQUESTED = "payment_requested"
    PAID = "paid"
    CASH_PAID = "cash_paid"
    FAILED = "failed"

class TaskBidStatus(str, enum.Enum):
    """Legacy proposal bidding statuses."""
    PENDING = "pending"
    WITHDRAWN = "withdrawn"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    EXPIRED = "expired"

class DispatchAttemptStatus(str, enum.Enum):
    """Workflow states of a 30-second provider dispatch ping attempt."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    TIMEOUT = "timeout"
    CANCELED = "canceled"

class DispatchSessionStatus(str, enum.Enum):
    """Workflow state of a task dispatch session."""
    SEARCHING = "SEARCHING"
    ASSIGNED = "ASSIGNED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class PriceAdjustmentStatus(str, enum.Enum):
    """Approval status for on-site task scope or fee adjustments."""
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"

class TaskAssignmentStatus(str, enum.Enum):
    """Status of an assigned provider working on a task."""
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class Task(SQLModel, table=True):
    """Primary task entity storing request details, location, upfront pricing breakdowns, and dispatch status."""
    __tablename__ = "tasks"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique primary identifier for the task")
    customer_id: Optional[str] = Field(default=None, foreign_key="users.id", index=True, ondelete="SET NULL", nullable=True, description="Foreign key reference to customer user ID")
    region_id: Optional[str] = Field(default=None, foreign_key="regions.id", index=True,ondelete="SET NULL",  nullable=True, description="Assigned geographical region ID")
    title: str = Field(nullable=False, description="Short descriptive title of the task")
    description: str = Field(nullable=False, description="Detailed scope description of the task request")
    category_id: Optional[str] = Field(default=None, foreign_key="categories.id", index=True,ondelete="SET NULL",  nullable=True, description="Foreign key reference to task category")
    service_id: Optional[str] = Field(default=None, foreign_key="services.id", index=True,ondelete="SET NULL",  nullable=True, description="Foreign key reference to specific service")
    
    # Upfront Pricing Breakdown
    base_price: Optional[float] = Field(default=0.0, nullable=True, description="Calculated upfront base price for task category/service")
    distance_fee: Optional[float] = Field(default=0.0, nullable=True, description="Calculated travel distance fee")
    time_fee: Optional[float] = Field(default=0.0, nullable=True, description="Calculated estimated duration labor fee")
    urgency_fee: Optional[float] = Field(default=0.0, nullable=True, description="Surcharge fee applied for same-day / instant requests")
    complexity_fee: Optional[float] = Field(default=0.0, nullable=True, description="Additional fee for special equipment or labor requirements")
    surge_multiplier: Optional[float] = Field(default=1.0, nullable=True, description="Dynamic demand-to-supply ratio surge multiplier")
    customer_total_price: Optional[float] = Field(default=0.0, nullable=True, description="Total locked-in price charged to customer")
    platform_fee: Optional[float] = Field(default=0.0, nullable=True, description="Platform commission fee retained (e.g. 15%)")
    provider_payout: Optional[float] = Field(default=0.0, nullable=True, description="Net earnings paid out to assigned provider")

    # Dispatch & Assignment State
    assigned_provider_id: Optional[str] = Field(default=None, foreign_key="users.id", index=True, ondelete="SET NULL", nullable=True, description="Foreign key of provider accepted and assigned to task")
    status: TaskStatus = Field(default=TaskStatus.OPEN, index=True, description="Current lifecycle status of the task")
    dispatch_started_at: Optional[datetime] = Field(default=None, nullable=True, description="Timestamp when cascading dispatch loop was initiated")
    current_attempt_sequence: Optional[int] = Field(default=0, nullable=True, description="Current attempt number in candidate dispatch queue")
    scheduled_start_at: Optional[datetime] = Field(default=None, nullable=True, description="Target scheduled start time if not immediate")
    start_pin: Optional[str] = Field(default=None, nullable=True, description="Secure 4-digit verification PIN to initiate task on-site")
    completion_pin: Optional[str] = Field(default=None, nullable=True, description="Secure 4-digit verification PIN to complete task on-site")
    # Payment Settlement State
    payment_mode: Optional[PaymentMode] = Field(default=None, index=True, nullable=True, description="Settlement mode selected on completion (cash or online)")
    payment_status: Optional[PaymentStatus] = Field(default=PaymentStatus.PENDING, index=True, nullable=True, description="Payment settlement status")
    payment_url: Optional[str] = Field(default=None, nullable=True, description="Paystack checkout URL generated for online payments")

    expires_at: Optional[datetime] = Field(default=None, index=True, nullable=True, description="Expiration timestamp if unassigned after dispatch")
    created_at: datetime = Field(default_factory=lagos_now, index=True, description="Record creation timestamp")
    updated_at: datetime = Field(default_factory=lagos_now, description="Record update timestamp")

    # Relationships
    locations: List["TaskLocation"] = Relationship(
        back_populates="task",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "lazy": "joined",
        },
    )
    price_adjustments: List["TaskPriceAdjustment"] = Relationship(
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
    # pyrefly: ignore [unknown-name]
    category: Optional["ServiceCategory"] = Relationship(
        sa_relationship_kwargs={"lazy": "joined"}
    )

    @property
    def distance_km(self) -> Optional[float]:
        if self.locations:
            for loc in self.locations:
                if getattr(loc, "distance_km", None) is not None:
                    return loc.distance_km
        return None

class DispatchSession(SQLModel, table=True):
    """Tracks a stateful multi-step matching engine dispatch session for a task."""
    __tablename__ = "dispatch_sessions"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique dispatch session ID")
    task_id: str = Field(foreign_key="tasks.id", index=True, ondelete="CASCADE", description="Foreign key reference to task being dispatched")
    status: DispatchSessionStatus = Field(default=DispatchSessionStatus.SEARCHING, index=True, description="Current workflow state of the dispatch session")
    batch_size: int = Field(default=5, description="Number of candidate pings to process per step")
    current_batch: int = Field(default=1, description="Current batch step iteration number")
    created_at: datetime = Field(default_factory=lagos_now, description="Record creation timestamp")
    updated_at: datetime = Field(default_factory=lagos_now, description="Record update timestamp")

    task: Task = Relationship()
    dispatch_attempts: List["TaskDispatchAttempt"] = Relationship(
        back_populates="dispatch_session",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )

class TaskLocation(SQLModel, table=True):
    """Geographical address and PostGIS coordinate point associated with a task."""
    __tablename__ = "task_locations"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique location entry ID")
    task_id: str = Field(foreign_key="tasks.id", index=True, ondelete="CASCADE", description="Foreign key reference to associated task")
    location_type: LocationType = Field(default=LocationType.SERVICE, index=True, description="Type of location point (service, pickup, dropoff)")
    latitude: float = Field(description="WGS84 Latitude coordinate")
    longitude: float = Field(description="WGS84 Longitude coordinate")
    address: Optional[str] = Field(default=None, nullable=True, description="Formatted street address string")
    city: Optional[str] = Field(default=None, nullable=True, description="City name")
    state: Optional[str] = Field(default=None, nullable=True, description="State/Province name")
    country: Optional[str] = Field(default=None, nullable=True, description="Country name")
    geography_point: Optional[Any] = Field(
        default=None, sa_column=Column(PointType, nullable=True), description="PostGIS Point spatial geography column"
    )
    distance_km: Optional[float] = Field(default=None, sa_column=query_expression(), description="Dynamically computed distance from spatial queries")  # type: ignore
    created_at: datetime = Field(default_factory=lagos_now, description="Record creation timestamp")
    updated_at: datetime = Field(default_factory=lagos_now, description="Record update timestamp")

    task: Task = Relationship(back_populates="locations")

class TaskDispatchAttempt(SQLModel, table=True):
    """Logs individual 30-second dispatch pings sent to candidate providers during cascading dispatch."""
    __tablename__ = "task_dispatch_attempts"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique dispatch attempt ID")
    dispatch_session_id: Optional[str] = Field(
        default=None,
        foreign_key="dispatch_sessions.id",
        index=True,
        ondelete="CASCADE",
        nullable=True,
        description="Foreign key reference to parent dispatch session",
    )
    task_id: str = Field(foreign_key="tasks.id", index=True, ondelete="CASCADE", description="Foreign key reference to task being dispatched")
    provider_id: str = Field(foreign_key="users.id", index=True, ondelete="CASCADE", description="Foreign key reference to provider candidate receiving ping")
    sequence_order: Optional[int] = Field(default=1, nullable=True, description="Position order in the ranked candidate queue")
    match_score: Optional[float] = Field(default=0.0, nullable=True, description="Composite score ranking evaluated by matching engine")
    offered_payout: Optional[float] = Field(default=0.0, nullable=True, description="Net provider payout offered for accepting ping")
    pinged_at: Optional[datetime] = Field(default_factory=lagos_now, nullable=True, description="Timestamp when dispatch ping notification was dispatched")
    expires_at: Optional[datetime] = Field(default=None, nullable=True, description="Expiration timestamp (typically pinged_at + 30 seconds)")
    responded_at: Optional[datetime] = Field(default=None, nullable=True, description="Timestamp when provider responded or ping timed out")
    status: Optional[DispatchAttemptStatus] = Field(default=DispatchAttemptStatus.PENDING, index=True, nullable=True, description="Outcome status of dispatch ping attempt")

    task: Task = Relationship()
    dispatch_session: Optional[DispatchSession] = Relationship(back_populates="dispatch_attempts")


class TaskPriceAdjustment(SQLModel, table=True):
    """Tracks on-site price adjustments or additional line items requested during task execution."""
    __tablename__ = "task_price_adjustments"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique price adjustment ID")
    task_id: str = Field(foreign_key="tasks.id", index=True, ondelete="CASCADE", description="Foreign key reference to associated task")
    description: Optional[str] = Field(default=None, nullable=True, description="Explanation for extra materials or unexpected labor")
    amount: Optional[float] = Field(default=None, nullable=True, description="Additional price amount requested")
    requested_by: Optional[str] = Field(default=None, foreign_key="users.id", index=True, nullable=True, description="Foreign key of user requesting adjustment")
    status: Optional[PriceAdjustmentStatus] = Field(default=PriceAdjustmentStatus.PENDING_APPROVAL, index=True, nullable=True, description="Customer approval status of price adjustment")
    created_at: datetime = Field(default_factory=lagos_now, description="Record creation timestamp")

    task: Task = Relationship(back_populates="price_adjustments")

class TaskAssignment(SQLModel, table=True):
    """Active assignment contract linking an accepted provider to a task."""
    __tablename__ = "task_assignments"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique task assignment ID")
    task_id: str = Field(foreign_key="tasks.id", unique=True, index=True, ondelete="CASCADE", description="Foreign key reference to associated task")
    provider_id: str = Field(foreign_key="users.id", index=True, ondelete="CASCADE", description="Foreign key reference to assigned provider")
    accepted_dispatch_attempt_id: Optional[str] = Field(
        default=None, foreign_key="task_dispatch_attempts.id", nullable=True, description="Foreign key of accepted dispatch attempt ping"
    )
    accepted_price: Optional[float] = Field(default=None, nullable=True, description="Agreed upfront provider payout price")
    assigned_at: datetime = Field(default_factory=lagos_now, description="Timestamp when provider accepted assignment")
    started_at: Optional[datetime] = Field(default=None, nullable=True, description="Timestamp when provider initiated work on-site")
    completed_at: Optional[datetime] = Field(default=None, nullable=True, description="Timestamp when task work was finished and verified")
    status: TaskAssignmentStatus = Field(default=TaskAssignmentStatus.ASSIGNED, description="Assignment status")

    task: Task = Relationship(back_populates="assignment")

class TaskStatusHistory(SQLModel, table=True):
    """Audit log tracking state transitions for tasks."""
    __tablename__ = "task_status_history"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique status history entry ID")
    task_id: str = Field(foreign_key="tasks.id", index=True, ondelete="CASCADE", description="Foreign key reference to task")
    old_status: Optional[TaskStatus] = Field(default=None, nullable=True, description="Previous task status before change")
    new_status: TaskStatus = Field(description="New task status after change")
    changed_by: Optional[str] = Field(default=None, foreign_key="users.id", nullable=True, description="User ID or system actor initiating status change")
    timestamp: datetime = Field(default_factory=lagos_now, description="Timestamp of status transition")

    task: Task = Relationship(back_populates="history")

class TaskAttachment(SQLModel, table=True):
    """Uploaded task attachments such as job photos, invoices, or document images."""
    __tablename__ = "task_attachments"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique task attachment ID")
    task_id: Optional[str] = Field(default=None,ondelete="SET NULL", nullable=True, foreign_key="tasks.id", index=True, description="The ID of the task this attachment is associated with")
    storage_key: str = Field(description="The unique object key/path inside cloud storage (e.g. 'tasks/task-uuid/image.png')")
    file_name: Optional[str] = Field(default=None, nullable=True, description="The original name of the uploaded file")
    file_size: Optional[int] = Field(default=None, nullable=True, description="The size of the file in bytes")
    mime_type: Optional[str] = Field(default=None, nullable=True, description="The standard internet media type of the file (e.g. 'image/jpeg', 'video/mp4')")
    url: Optional[str] = Field(default=None, nullable=True, description="The public-facing URL to access/download the file")
    type: Optional[str] = Field(default=None, nullable=True, description="Semantic workflow category of the file (e.g. 'before_photo', 'after_photo', 'invoice')")
    created_at: datetime = Field(default_factory=lagos_now, description="Record creation timestamp")

    task: Task = Relationship(back_populates="attachments")
