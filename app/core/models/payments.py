from app.core.models import Task
import enum
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel, Relationship

from app.core.utils.datetime_helper import utc_now


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
    created_at: datetime = Field(default_factory=utc_now, description="Record creation timestamp")

    task: Optional["Task"] = Relationship(sa_relationship_kwargs={"lazy": "joined"})
