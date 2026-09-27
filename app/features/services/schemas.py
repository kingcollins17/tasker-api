from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    default_base_price: Optional[float] = None
    default_duration_min: Optional[int] = None
    per_km_rate: Optional[float] = None
    per_minute_rate: Optional[float] = None
    is_active: Optional[bool] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CreateCategoryRequest(BaseModel):
    name: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    default_base_price: Optional[float] = 0.0
    default_duration_min: Optional[int] = 60
    per_km_rate: Optional[float] = 150.0
    per_minute_rate: Optional[float] = 20.0
    is_active: Optional[bool] = True


class UpdateCategoryRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    default_base_price: Optional[float] = None
    default_duration_min: Optional[int] = None
    per_km_rate: Optional[float] = None
    per_minute_rate: Optional[float] = None
    is_active: Optional[bool] = None


class ServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    name: Optional[str] = None
    image_url: Optional[str] = None
    base_price: Optional[float] = None
    default_duration_min: Optional[int] = None
    per_km_rate: Optional[float] = None
    per_minute_rate: Optional[float] = None
    take_rate: Optional[float] = None
    min_tier_required: Optional[int] = None
    is_high_risk: Optional[bool] = None
    is_active: Optional[bool] = None
    category_id: Optional[str] = None
    category: Optional[CategoryResponse] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CreateServiceRequest(BaseModel):
    name: str
    category_id: Optional[str] = None
    image_url: Optional[str] = None
    base_price: Optional[float] = 0.0
    default_duration_min: Optional[int] = 60
    per_km_rate: Optional[float] = 150.0
    per_minute_rate: Optional[float] = 20.0
    take_rate: Optional[float] = 0.15
    min_tier_required: Optional[int] = 4
    is_high_risk: Optional[bool] = False
    is_active: Optional[bool] = True


class UpdateServiceRequest(BaseModel):
    name: Optional[str] = None
    category_id: Optional[str] = None
    image_url: Optional[str] = None
    base_price: Optional[float] = None
    default_duration_min: Optional[int] = None
    per_km_rate: Optional[float] = None
    per_minute_rate: Optional[float] = None
    take_rate: Optional[float] = None
    min_tier_required: Optional[int] = None
    is_high_risk: Optional[bool] = None
    is_active: Optional[bool] = None


class ServiceAvailabilityResponse(BaseModel):
    is_available: bool


class BulkServiceAvailabilityItem(BaseModel):
    service_id: Optional[str] = None
    service_name: Optional[str] = None
    is_available: Optional[bool] = None
    provider_count: Optional[int] = None


class AvailableServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service_id: Optional[str] = None
    service_name: Optional[str] = None
    image_url: Optional[str] = None
    take_rate: Optional[float] = None
    is_active: Optional[bool] = None
    category_id: Optional[str] = None
    category: Optional[CategoryResponse] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_available: Optional[bool] = None
    provider_count: Optional[int] = None
