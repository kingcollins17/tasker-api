from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime
from app.core.models.tasks import TaskStatus, TaskBidStatus, TaskAssignmentStatus, LocationType
from app.core.schemas.users import MinimalProviderResponse

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
    budget_min: Optional[float] = Field(default=None, ge=0, description="Minimum budget")
    budget_max: Optional[float] = Field(default=None, ge=0, description="Maximum budget")
    pricing_model: Optional[str] = Field(default="fixed", description="Pricing model, e.g. fixed or hourly")
    expires_at: Optional[datetime] = Field(default=None, description="Expiration date/time of the task")
    scheduled_start_at: Optional[datetime] = Field(default=None, description="When the user would like the task to start")
    
    locations: List[LocationCreate] = Field(..., min_length=1, max_length=2, description="List of task locations (1 or 2)")

class TaskUpdate(BaseModel):
    title: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    category_id: Optional[str] = Field(default=None)
    service_id: Optional[str] = Field(default=None)
    budget_min: Optional[float] = Field(default=None, ge=0)
    budget_max: Optional[float] = Field(default=None, ge=0)
    pricing_model: Optional[str] = Field(default=None)
    expires_at: Optional[datetime] = Field(default=None)
    scheduled_start_at: Optional[datetime] = Field(default=None)
    status: Optional[TaskStatus] = Field(default=None)
    
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

# Task Bid Response (referenced in TaskResponse)
class TaskBidResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: Optional[str] = None
    task_id: Optional[str] = None
    provider_id: Optional[str] = None
    price: Optional[float] = None
    message: Optional[str] = None
    estimated_duration: Optional[str] = None
    status: Optional[TaskBidStatus] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class TaskMinimalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    title: Optional[str] = None
    category_id: Optional[str] = None
    service_id: Optional[str] = None
    status: Optional[TaskStatus] = None

class TaskBidWithTaskResponse(TaskBidResponse):
    task: Optional[TaskMinimalResponse] = None

class TaskBidWithProviderResponse(TaskBidResponse):
    provider: Optional[MinimalProviderResponse] = None

# Task Assignment Response (referenced in TaskResponse)
class TaskAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: Optional[str] = None
    task_id: Optional[str] = None
    provider_id: Optional[str] = None
    accepted_bid_id: Optional[str] = None
    accepted_price: Optional[float] = None
    assigned_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: Optional[TaskAssignmentStatus] = None

class TaskAssignmentWithTaskResponse(TaskAssignmentResponse):
    task: Optional[TaskMinimalResponse] = None

# Task Attachment Response
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

# Task List Response
class TaskListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: Optional[str] = None
    customer_id: Optional[str] = None
    title: Optional[str] = None
    category_id: Optional[str] = None
    service_id: Optional[str] = None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    pricing_model: Optional[str] = None
    status: Optional[TaskStatus] = None
    created_at: Optional[datetime] = None
    scheduled_start_at: Optional[datetime] = None
    distance_km: Optional[float] = None

# Task Response
class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: Optional[str] = None
    customer_id: Optional[str] = None
    region_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[str] = None
    service_id: Optional[str] = None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    pricing_model: Optional[str] = None
    status: Optional[TaskStatus] = None
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    scheduled_start_at: Optional[datetime] = None
    start_pin: Optional[str] = None
    completion_pin: Optional[str] = None
    updated_at: Optional[datetime] = None
    locations: Optional[List[TaskLocationResponse]] = None
    bids: Optional[List[TaskBidResponse]] = None
    assignment: Optional[TaskAssignmentResponse] = None
    attachments: Optional[List[TaskAttachmentResponse]] = None


# Bids Schemas
class TaskBidCreate(BaseModel):
    price: float = Field(..., ge=0, description="Bid amount")
    message: Optional[str] = Field(default=None, description="Message to the customer")
    estimated_duration: Optional[str] = Field(default=None, description="Estimated work duration, e.g. 2 hours")

class TaskBidUpdate(BaseModel):
    price: Optional[float] = Field(None, ge=0, description="Bid amount")
    message: Optional[str] = Field(None, description="Message to the customer")
    estimated_duration: Optional[str] = Field(None, description="Estimated work duration, e.g. 2 hours")
