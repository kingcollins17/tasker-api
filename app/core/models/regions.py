from uuid import uuid4
from datetime import datetime
from app.core.utils.datetime_helper import lagos_now
from typing import Optional, Any
from sqlmodel import Field, SQLModel
from sqlalchemy import Column
from .spatial import GeometryType

class Region(SQLModel, table=True):
    __tablename__ = "regions"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    address_line: Optional[str] = Field(default=None)
    state: str = Field(index=True)
    is_active: bool = Field(default=True)
    total_providers: int = Field(default=0)
    total_customers: int = Field(default=0)
    total_tasks: int = Field(default=0)
    total_staff: int = Field(default=0)
    location: Optional[Any] = Field(
        default=None,
        sa_column=Column(GeometryType, nullable=True)
    )
    created_at: datetime = Field(default_factory=lagos_now)
    updated_at: datetime = Field(default_factory=lagos_now)

