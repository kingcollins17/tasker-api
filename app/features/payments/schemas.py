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
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    pricing_model: Optional[str] = None
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
