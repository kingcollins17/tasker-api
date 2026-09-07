from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field

from app.core.models.tasks import PaymentStatus, TaskAssignmentStatus, TaskStatus, DispatchAttemptStatus, CancelledBy
from app.core.schemas.users import MinimalCustomerResponse, MinimalProviderResponse
from app.features.services.schemas import CategoryResponse

class TaskLocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    task_id: Optional[str] = None
    location_type: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    distance_km: Optional[float] = None


class TaskMinimalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[str] = None
    service_id: Optional[str] = None
    status: Optional[TaskStatus] = None
    scheduled_start_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    provider_payout: Optional[float] = None
    customer_total_price: Optional[float] = None


class TaskAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    task_id: Optional[str] = None
    provider_id: Optional[str] = None
    accepted_dispatch_attempt_id: Optional[str] = None
    accepted_price: Optional[float] = None
    assigned_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    pin: Optional[str] = None
    cancellation_pin: Optional[str] = None
    status: Optional[TaskAssignmentStatus] = None


class TaskAssignmentWithTaskResponse(TaskAssignmentResponse):
    task: Optional[TaskMinimalResponse] = None
    provider: Optional[MinimalProviderResponse] = None


class TaskAttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    task_id: Optional[str] = None
    storage_key: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    url: Optional[str] = None
    type: Optional[str] = None
    created_at: Optional[datetime] = None


class TaskListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    customer_id: Optional[str] = None
    title: Optional[str] = None
    category_id: Optional[str] = None
    service_id: Optional[str] = None
    base_price: Optional[float] = None
    distance_fee: Optional[float] = None
    time_fee: Optional[float] = None
    urgency_fee: Optional[float] = None
    complexity_fee: Optional[float] = None
    surge_multiplier: Optional[float] = None
    customer_total_price: Optional[float] = None
    platform_fee: Optional[float] = None
    provider_payout: Optional[float] = None
    status: Optional[TaskStatus] = None
    created_at: Optional[datetime] = None
    scheduled_start_at: Optional[datetime] = None
    distance_km: Optional[float] = None
    category: Optional[CategoryResponse] = None
    assignment: Optional[TaskAssignmentResponse] = None

class PendingReviewTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    customer_id: Optional[str] = None
    title: Optional[str] = None
    category_id: Optional[str] = None
    service_id: Optional[str] = None
    base_price: Optional[float] = None
    distance_fee: Optional[float] = None
    time_fee: Optional[float] = None
    urgency_fee: Optional[float] = None
    complexity_fee: Optional[float] = None
    surge_multiplier: Optional[float] = None
    customer_total_price: Optional[float] = None
    platform_fee: Optional[float] = None
    provider_payout: Optional[float] = None
    status: Optional[TaskStatus] = None
    created_at: Optional[datetime] = None
    scheduled_start_at: Optional[datetime] = None
    distance_km: Optional[float] = None
    customer: Optional[MinimalCustomerResponse] = None
    provider: Optional[MinimalProviderResponse] = None


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    customer_id: Optional[str] = None
    region_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[str] = None
    service_id: Optional[str] = None
    base_price: Optional[float] = None
    distance_fee: Optional[float] = None
    time_fee: Optional[float] = None
    urgency_fee: Optional[float] = None
    complexity_fee: Optional[float] = None
    surge_multiplier: Optional[float] = None
    customer_total_price: Optional[float] = None
    platform_fee: Optional[float] = None
    provider_payout: Optional[float] = None
    status: Optional[TaskStatus] = None
    payment_status: Optional[PaymentStatus] = None
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    scheduled_start_at: Optional[datetime] = None
    start_pin: Optional[str] = None
    completion_pin: Optional[str] = None
    updated_at: Optional[datetime] = None
    cancellation_reason: Optional[str] = None
    cancelled_by: Optional[CancelledBy] = None
    locations: Optional[List[TaskLocationResponse]] = None
    assignment: Optional[TaskAssignmentResponse] = None
    attachments: Optional[List[TaskAttachmentResponse]] = None
    customer: Optional[MinimalCustomerResponse] = None


class TaskDispatchAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    task_id: Optional[str] = None
    provider_id: Optional[str] = None
    sequence_order: Optional[int] = None
    match_score: Optional[float] = None
    offered_payout: Optional[float] = None
    pinged_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    responded_at: Optional[datetime] = None
    status: Optional[DispatchAttemptStatus] = None
    provider: Optional[MinimalProviderResponse] = None
