from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field

from app.core.models.tasks import (
    LocationType,
    PaymentStatus,
    TaskAssignmentStatus,
    TaskStatus,
    DispatchAttemptStatus,
    CancelledBy,
    PriceAdjustmentStatus,
)
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


class PriceAdjustmentCreate(BaseModel):
    amount: float = Field(..., gt=0, description="Additional price amount requested")
    description: Optional[str] = Field(default=None, description="Explanation for price adjustment")


class PriceAdjustmentRespond(BaseModel):
    approved: bool = Field(..., description="True to accept/approve, False to decline/reject")


class TaskPriceAdjustmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    task_id: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[float] = None
    requested_by: Optional[str] = None
    status: Optional[PriceAdjustmentStatus] = None
    created_at: Optional[datetime] = None




