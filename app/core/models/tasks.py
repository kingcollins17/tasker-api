import enum
import random
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import Column, Index, JSON, null
from sqlalchemy.orm import query_expression
from sqlmodel import Field, Relationship, SQLModel

from app.core.models.spatial import PointType
from app.core.utils.datetime_helper import lagos_now


def generate_4digit_pin() -> str:
    """Generate a random 4-digit PIN string."""
    return f"{random.randint(0, 9999):04d}"

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
    PENDING = "PENDING"
    PAYMENT_REQUESTED = "PAYMENT_REQUESTED"
    CUSTOMER_PAID = "CUSTOMER_PAID"
    TRANSFER_INITIATED = "TRANSFER_INITIATED"
    PAID = "PAID"
    CASH_PAID = "CASH_PAID"
    FAILED = "FAILED"


class DispatchAttemptStatus(str, enum.Enum):
    """Workflow states of a n-second provider dispatch ping attempt."""
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    TIMEOUT = "TIMEOUT"
    CANCELED = "CANCELED"

class DispatchSessionStatus(str, enum.Enum):
    """Workflow state of a task dispatch session."""
    SEARCHING = "SEARCHING"
    ASSIGNED = "ASSIGNED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class PriceAdjustmentStatus(str, enum.Enum):
    """Approval status for on-site task scope or fee adjustments."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class TaskAssignmentStatus(str, enum.Enum):
    """Status of an assigned provider working on a task."""
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

class CancelledBy(str, enum.Enum):
    """Who initiated task cancellation."""
    PLATFORM = "PLATFORM"
    CUSTOMER = "CUSTOMER"

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
    
    max_customer_redispatches: Optional[int] = Field(default=3, nullable=True, description="Maximum number of redispatches a customer is allowed")
    
    scheduled_start_at: Optional[datetime] = Field(default=None, nullable=True, description="Target scheduled start time if not immediate")
    start_pin: Optional[str] = Field(default_factory=generate_4digit_pin, nullable=True, description="Secure 4-digit verification PIN to initiate task on-site")
    completion_pin: Optional[str] = Field(default_factory=generate_4digit_pin, nullable=True, description="Secure 4-digit verification PIN to complete task on-site")
    cancellation_reason: Optional[str] = Field(default=None, nullable=True, description="Reason for task cancellation")
    cancelled_by: Optional[CancelledBy] = Field(default=None, index=True, nullable=True, description="Who initiated the cancellation (platform or customer)")
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
    events: List["TaskEventHistory"] = Relationship(
        back_populates="task",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )
    attachments: List["TaskAttachment"] = Relationship(
        back_populates="task",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )
    # pyrefly: ignore [unknown-name]
    category: Optional["ServiceCategory"] = Relationship( # type: ignore
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
    lock_version: int = Field(
        default=1,
        description="Optimistic-concurrency version counter. Incremented exactly once per `run()` step, by run() only. Never read or written by pagination logic.",
    )
    search_radius_km: Optional[float] = Field(
        default=10.0,
        nullable=True,
        description="Current search radius in kilometers for provider candidate matching",
    )
    max_search_radius_km: Optional[float] = Field(
        default=30.0,
        nullable=True,
        description="Maximum search radius limit in kilometers for expanding provider search",
    )
    auto_expand_radius: Optional[bool] = Field(
        default=True,
        nullable=True,
        description="Tracks whether this dispatch session should keep increasing search radius up to max_search_radius_km if previous PostGIS searches do not return candidates",
    )
    is_redispatch: Optional[bool] = Field(default=False, nullable=True, description="Tracks whether the dispatch was automatically started by the system (False) or is a redispatch by the customer (True)")
    redispatch_reason: Optional[str] = Field(default=None, nullable=True, description="Optional rationale or customer feedback for requesting a task redispatch")
    excluded_provider_ids: Optional[List[str]] = Field(
        default=None, sa_column=Column(JSON, nullable=True), description="Optional list of provider IDs to exclude from candidate matching during this dispatch session"
    )
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
    __table_args__ = (
        Index("idx_task_locations_spatial", "geography_point", postgresql_using="gist"),
    )

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
    status: Optional[PriceAdjustmentStatus] = Field(default=PriceAdjustmentStatus.PENDING, index=True, nullable=True, description="Customer approval status of price adjustment")
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
    pin: Optional[str] = Field(default_factory=generate_4digit_pin, nullable=True, description="Secure 4-digit verification PIN generated for the assignment")
    cancellation_pin: Optional[str] = Field(default_factory=generate_4digit_pin, nullable=True, description="Secure 4-digit PIN for customer to cancel task with agreement from provider")
    status: TaskAssignmentStatus = Field(default=TaskAssignmentStatus.ASSIGNED, description="Assignment status")
    created_at: datetime = Field(default_factory=lagos_now, description="Record creation timestamp")
    updated_at: datetime = Field(default_factory=lagos_now, description="Record update timestamp")

    task: Task = Relationship(back_populates="assignment")

class TaskEventHistory(SQLModel, table=True):
    """Event log capturing task lifecycle actions, reasons, and structured payloads."""
    __tablename__ = "task_event_history"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique task event history ID")
    task_id: str = Field(foreign_key="tasks.id", index=True, ondelete="CASCADE", description="Foreign key reference to task")
    event: str = Field(description="Task lifecycle event name")
    reason: Optional[str] = Field(default=None, nullable=True, description="Human-readable reason or rationale for this event")
    data: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Structured event payload containing richer context such as status transitions and metadata",
    )
    created_at: datetime = Field(default_factory=lagos_now, description="Timestamp of the event")

    task: Task = Relationship(back_populates="events")

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
