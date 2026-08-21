import enum
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, Relationship, SQLModel

from app.core.utils.datetime_helper import lagos_now


class TransferStatus(str, enum.Enum):
    """Lifecycle states of a provider transfer."""
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TransferErrorType(str, enum.Enum):
    """Classification of transfer provider errors for retry decisions."""
    TEMPORARY = "TEMPORARY"
    PERMANENT = "PERMANENT"
    UNKNOWN = "UNKNOWN"


# ── Valid state transitions for the Transfer state machine ────────────────────
VALID_TRANSFER_TRANSITIONS: dict[TransferStatus, set[TransferStatus]] = {
    TransferStatus.PENDING: {TransferStatus.PROCESSING, TransferStatus.CANCELLED},
    TransferStatus.PROCESSING: {
        TransferStatus.COMPLETED,
        TransferStatus.RETRYING,
        TransferStatus.FAILED,
    },
    TransferStatus.RETRYING: {TransferStatus.PROCESSING, TransferStatus.CANCELLED},
    # Terminal states — no outgoing transitions
    TransferStatus.COMPLETED: set(),
    TransferStatus.FAILED: set(),
    TransferStatus.CANCELLED: set(),
}


class Transfer(SQLModel, table=True):
    """Durable record of an intended money movement from the platform to a provider.

    The database is the source of truth — not the payment provider.
    Each Transfer tracks intent, attempt history, provider confirmation,
    and retry/failure state so the system can recover from crashes,
    timeouts, and duplicate webhook deliveries.
    """
    __tablename__ = "transfers"  # type: ignore

    id: str = Field(
        default_factory=lambda: str(uuid4()),
        primary_key=True,
        description="Unique transfer ID",
    )
    task_id: Optional[str] = Field(
        default=None,
        foreign_key="tasks.id",
        index=True,
        ondelete="SET NULL",
        nullable=True,
        description="Associated task that generated this transfer",
    )
    payment_id: Optional[str] = Field(
        default=None,
        foreign_key="payout_queue.id",
        index=True,
        unique=True,
        nullable=True,
        description="Associated payout queue entry — unique constraint prevents duplicate transfers per payment",
    )
    provider_id: Optional[str] = Field(
        default=None,
        foreign_key="users.id",
        index=True,
        ondelete="SET NULL",
        nullable=True,
        description="Provider (user) who will receive the transferred funds",
    )
    amount: float = Field(
        description="Transfer amount in the specified currency",
    )
    currency: str = Field(
        default="NGN",
        description="ISO 4217 currency code",
    )

    # ── State machine fields ──────────────────────────────────────────────
    status: TransferStatus = Field(
        default=TransferStatus.PENDING,
        index=True,
        description="Current lifecycle status of this transfer",
    )
    attempt_count: int = Field(
        default=0,
        description="Number of processing attempts made so far",
    )
    max_attempts: int = Field(
        default=10,
        description="Maximum allowed processing attempts before permanent failure",
    )

    # ── Provider response tracking ────────────────────────────────────────
    provider_transfer_id: Optional[str] = Field(
        default=None,
        nullable=True,
        index=True,
        description="External transfer ID returned by the payment provider",
    )
    idempotency_key: str = Field(
        unique=True,
        index=True,
        description="Unique key sent to the provider on every attempt to prevent double-transfers",
    )

    # ── Scheduling & timing ───────────────────────────────────────────────
    next_retry_at: Optional[datetime] = Field(
        default=None,
        nullable=True,
        index=True,
        description="Earliest time the recovery worker should re-attempt this transfer",
    )
    last_attempt_at: Optional[datetime] = Field(
        default=None,
        nullable=True,
        description="Timestamp of the most recent processing attempt",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        nullable=True,
        description="Timestamp when the provider confirmed successful transfer",
    )
    failed_at: Optional[datetime] = Field(
        default=None,
        nullable=True,
        description="Timestamp when the transfer was permanently marked as failed",
    )

    # ── Failure diagnostics ───────────────────────────────────────────────
    failure_code: Optional[str] = Field(
        default=None,
        nullable=True,
        description="Machine-readable failure code from the provider or system",
    )
    failure_reason: Optional[str] = Field(
        default=None,
        nullable=True,
        description="Human-readable failure description",
    )

    # ── Optimistic concurrency ────────────────────────────────────────────
    version: int = Field(
        default=0,
        description="Optimistic concurrency version counter — incremented on every state change",
    )

    created_at: datetime = Field(
        default_factory=lagos_now,
        description="Record creation timestamp",
    )
    updated_at: datetime = Field(
        default_factory=lagos_now,
        description="Record update timestamp",
    )

    # ── Relationships ─────────────────────────────────────────────────────
    attempts: list["TransferAttempt"] = Relationship(
        back_populates="transfer",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "lazy": "selectin",
            "order_by": "TransferAttempt.attempt_number",
        },
    )


class TransferAttempt(SQLModel, table=True):
    """Immutable audit log entry for a single transfer processing attempt.

    Every call to the payment provider creates one TransferAttempt,
    regardless of outcome. This gives a full history for debugging
    and money-movement auditing.
    """
    __tablename__ = "transfer_attempts"  # type: ignore

    id: str = Field(
        default_factory=lambda: str(uuid4()),
        primary_key=True,
        description="Unique attempt ID",
    )
    transfer_id: str = Field(
        foreign_key="transfers.id",
        index=True,
        ondelete="CASCADE",
        description="Parent transfer record",
    )
    attempt_number: int = Field(
        description="Sequential attempt number (1-based)",
    )
    status: str = Field(
        description="Outcome of this attempt (e.g. 'success', 'timeout', 'error', 'provider_error')",
    )
    provider_transfer_id: Optional[str] = Field(
        default=None,
        nullable=True,
        description="External transfer ID returned by the provider for this attempt",
    )
    request_id: Optional[str] = Field(
        default=None,
        nullable=True,
        description="Provider request/correlation ID for tracing",
    )
    error_code: Optional[str] = Field(
        default=None,
        nullable=True,
        description="Error code from the provider or system",
    )
    error_message: Optional[str] = Field(
        default=None,
        nullable=True,
        description="Error description from the provider or system",
    )
    started_at: datetime = Field(
        default_factory=lagos_now,
        description="When this attempt started",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        nullable=True,
        description="When this attempt finished (success or failure)",
    )
    created_at: datetime = Field(
        default_factory=lagos_now,
        description="Record creation timestamp",
    )

    transfer: Transfer = Relationship(back_populates="attempts")
