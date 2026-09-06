from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field

from app.core.models.tasks import LocationType, PaymentStatus, TaskAssignmentStatus, TaskStatus, DispatchAttemptStatus, CancelledBy
from app.core.schemas.users import MinimalCustomerResponse, MinimalProviderResponse
from app.features.services.schemas import CategoryResponse


# Tasks Schemas
class LocationCreate(BaseModel):
    location_type: LocationType = Field(default=LocationType.SERVICE, description="Type of the location")
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Latitude of the task location")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Longitude of the task location")
    address: Optional[str] = Field(default=None, description="Address description")
    city: Optional[str] = Field(default=None, description="City")
    state: Optional[str] = Field(default=None, description="State")
    country: Optional[str] = Field(default=None, description="Country")


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, description="Title of the task")
    description: str = Field(..., min_length=1, description="Detailed description of the task")
    category_id: Optional[str] = Field(default=None, description="Category of the task")
    service_id: Optional[str] = Field(default=None, description="Specific service type of the task")
    expires_at: Optional[datetime] = Field(default=None, description="Expiration date/time of the task")
    scheduled_start_at: Optional[datetime] = Field(default=None, description="When the user would like the task to start")
    locations: List[LocationCreate] = Field(..., min_length=1, max_length=2, description="List of task locations (1 or 2)")


class TaskPriceEstimateRequest(BaseModel):
    category_id: Optional[str] = Field(default=None, description="Category ID of the task")
    service_id: Optional[str] = Field(default=None, description="Specific service ID of the task")
    is_urgent: Optional[bool] = Field(default=False, description="Whether immediate or same-day dispatch is requested")
    locations: Optional[List[LocationCreate]] = Field(default=None, description="List of task locations to compute distance fee")



class TaskUpdate(BaseModel):
    title: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    expires_at: Optional[datetime] = Field(default=None)
    scheduled_start_at: Optional[datetime] = Field(default=None)


class TaskCancellationRequest(BaseModel):
    cancellation_reason: Optional[str] = Field(default=None, description="Reason for cancelling the task")
    cancellation_pin: Optional[str] = Field(default=None, description="4-digit PIN for agreeing with provider to cancel task in progress (only for IN_PROGRESS tasks)")


class TaskRedispatchRequest(BaseModel):
    feedback: Optional[str] = Field(default=None, description="Optional feedback about why redispatch is needed (provider late, not a good fit, etc.)")


class TaskLocationUpdate(BaseModel):
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    address: Optional[str] = Field(default=None)
    city: Optional[str] = Field(default=None)
    state: Optional[str] = Field(default=None)
    country: Optional[str] = Field(default=None)


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
