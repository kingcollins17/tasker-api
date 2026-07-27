import enum
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel, Relationship

from app.core.utils.datetime_helper import lagos_now


class TransactionType(str, enum.Enum):
    TASK_PAYMENT = "task_payment"
    PROVIDER_PAYOUT = "provider_payout"
    CASH_COMMISSION_DEBT = "cash_commission_debt"
    DEBT_SETTLEMENT = "debt_settlement"
    REFUND = "refund"
    DISPUTE_SETTLEMENT = "dispute_settlement"


class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"  # type: ignore

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, index=True
    )
    amount: float = Field(
        description="Positive for incoming (e.g. payment), negative for outgoing (e.g. payout/refund/debt)"
    )
    transaction_type: TransactionType = Field(index=True)
    status: TransactionStatus = Field(index=True, default=TransactionStatus.PENDING)
    payment_mode: Optional[str] = Field(default=None, nullable=True, description="Payment mode: cash or online")
    user_id: Optional[str] = Field(default=None, foreign_key="users.id", ondelete="SET NULL", nullable=True, index=True)
    task_id: Optional[str] = Field(default=None, foreign_key="tasks.id", ondelete="SET NULL", nullable=True, index=True)
    reference: Optional[str] = Field(default=None, index=True, description="External payment gateway reference")
    metadata_info: Optional[Dict[str, Any]] = Field(
        default=None, sa_column=Column(JSON)
    )

    created_at: datetime = Field(default_factory=lagos_now)
    updated_at: datetime = Field(
        default_factory=lagos_now, sa_column_kwargs={"onupdate": lagos_now}
    )

    # pyrefly: ignore [unknown-name]
    task: Optional["Task"] = Relationship(
        sa_relationship_kwargs={"lazy": "joined"}
    )
