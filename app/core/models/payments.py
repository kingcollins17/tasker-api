from app.core.models import Task
import enum
from datetime import datetime
from typing import Optional, Dict, Any
from uuid import uuid4

from sqlmodel import Field, SQLModel, Relationship, Column, UniqueConstraint
from sqlalchemy import JSON

from app.core.utils.datetime_helper import lagos_now


class DebtReason(str, enum.Enum):
    """Reason for a provider debt ledger entry."""
    CASH_TASK_COMMISSION = "cash_task_commission"
    DEBT_PAYMENT = "debt_payment"
    PAYOUT_OFFSET = "payout_offset"
    WAIVER = "waiver"
    ADJUSTMENT = "adjustment"


class ProviderDebt(SQLModel, table=True):
    """Append-only ledger tracking provider debt entries (+ for debt accrued, - for debt paid/offset)."""
    __tablename__ = "provider_debts"  # type: ignore

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, index=True
    )
    provider_id: str = Field(
        foreign_key="users.id", index=True, ondelete="CASCADE", description="Provider associated with entry"
    )
    task_id: Optional[str] = Field(
        default=None, foreign_key="tasks.id", index=True, nullable=True, ondelete="SET NULL", description="Associated task if applicable"
    )
    amount: float = Field(
        description="Positive (+) for debt accrued, negative (-) for debt paid or offset"
    )
    reason: DebtReason = Field(
        default=DebtReason.CASH_TASK_COMMISSION, index=True, description="Reason for entry"
    )
    description: Optional[str] = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=lagos_now, description="Record creation timestamp")

    task: Optional["Task"] = Relationship(sa_relationship_kwargs={"lazy": "joined"})


class PayoutStatus(str, enum.Enum):
    """Status of a provider payout in the queue."""
    PENDING = "pending"
    CUSTOMER_PAID = "customer_paid"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PayoutQueue(SQLModel, table=True):
    """Queue for storing promised payments to be processed out to providers."""
    __tablename__ = "payout_queue"  # type: ignore
    __table_args__ = (
        UniqueConstraint("task_id", "provider_id", "customer_id", name="uq_payout_queue_task_provider_customer"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, index=True
    )
    provider_id: Optional[str] = Field(
        default=None, foreign_key="users.id", index=True, nullable=True, ondelete="SET NULL", description="Provider to receive the payout"
    )
    customer_id: Optional[str] = Field(
        default=None, foreign_key="users.id", index=True, nullable=True, ondelete="SET NULL", description="Customer who initiated the task/payment (if applicable)"
    )
    task_id: Optional[str] = Field(
        default=None, foreign_key="tasks.id", index=True, nullable=True, ondelete="SET NULL", description="Associated task if applicable"
    )
    payout_amount: float = Field(
        description="Amount to be paid out to the provider",
        gt=0.0,
    )
    customer_payment_amount: float = Field(
        default=0.0,
        description="Amount the customer was charged for the task"
    )
    status: PayoutStatus = Field(
        default=PayoutStatus.PENDING, index=True, description="Current status of the payout"
    )
    description: Optional[str] = Field(
        default=None, nullable=True, description="Human readable description for this payout"
    )
    data: Optional[Dict[str, Any]] = Field(
        default=None, sa_column=Column(JSON), description="Additional JSON metadata (e.g., breakdown, bank details)"
    )
    payment_url: Optional[str] = Field(
        default=None, nullable=True, description="URL for the payout gateway (if applicable)"
    )
    url_generated_at: Optional[datetime] = Field(
        default=None, nullable=True, description="Timestamp when the payment_url was generated"
    )
    reference: Optional[str] = Field(
        default=None, index=True, nullable=True, description="External payout reference (e.g., Paystack transfer reference)"
    )
   
    created_at: datetime = Field(
        default_factory=lagos_now, description="Record creation timestamp"
    )
    updated_at: datetime = Field(
        default_factory=lagos_now, sa_column_kwargs={"onupdate": lagos_now}, description="Record update timestamp"
    )

    task: Optional["Task"] = Relationship(sa_relationship_kwargs={"lazy": "joined"})
