from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class RegionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    address_line: Optional[str] = None
    state: Optional[str] = None
    is_active: Optional[bool] = None
    total_providers: Optional[int] = None
    total_customers: Optional[int] = None
    total_tasks: Optional[int] = None
    total_staff: Optional[int] = None
    location: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
