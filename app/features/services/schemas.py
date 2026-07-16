from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    is_active: Optional[bool] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    name: Optional[str] = None
    image_url: Optional[str] = None
    take_rate: Optional[float] = None
    is_active: Optional[bool] = None
    category_id: Optional[str] = None
    category: Optional[CategoryResponse] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ServiceAvailabilityResponse(BaseModel):
    is_available: bool


