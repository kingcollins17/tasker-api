from typing import Optional, Any, Dict
from datetime import datetime
from pydantic import BaseModel, ConfigDict
from app.core.models.transactions import TransactionType, TransactionStatus

class TaskBaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[str] = None
    service_id: Optional[str] = None
    customer_total_price: Optional[float] = None
    platform_fee: Optional[float] = None
    provider_payout: Optional[float] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[str] = None
    amount: Optional[float] = None
    transaction_type: Optional[TransactionType] = None
    status: Optional[TransactionStatus] = None
    user_id: Optional[str] = None
    task_id: Optional[str] = None
    reference: Optional[str] = None
    metadata_info: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    task: Optional[TaskBaseResponse] = None


class SettleDebtRequest(BaseModel):
    """Payload for provider requesting to pay up commission debt."""
    amount: Optional[float] = None  # If None, defaults to total amount owed


class SettleDebtResponse(BaseModel):
    """Response containing Paystack checkout URL to pay up provider debt."""
    payment_url: Optional[str] = None
    reference: Optional[str] = None
    total_debt_owed: Optional[float] = None
    amount_to_pay: Optional[float] = None


class ProviderDebtSummaryResponse(BaseModel):
    """Summary of provider's total pending debt balance."""
    total_debt_owed: Optional[float] = None
    pending_debts_count: Optional[int] = None

from app.core.models.payments import PayoutStatus

class PayoutQueueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[str] = None
    provider_id: Optional[str] = None
    customer_id: Optional[str] = None
    task_id: Optional[str] = None
    payout_amount: Optional[float] = None
    customer_payment_amount: Optional[float] = None
    status: Optional[PayoutStatus] = None
    description: Optional[str] = None
    payment_url: Optional[str] = None
    url_generated_at: Optional[datetime] = None
    reference: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    task: Optional[TaskBaseResponse] = None

class CustomerPayoutStatsResponse(BaseModel):
    total_payouts: Optional[int] = 0
    total_pending: Optional[int] = 0
    total_amount_pending: Optional[float] = 0.0
    total_completed: Optional[int] = 0
    total_amount_completed: Optional[float] = 0.0

class ProviderEarningStatsResponse(BaseModel):
    total_earnings: Optional[float] = 0.0
    percentage_growth: Optional[float] = 0.0

