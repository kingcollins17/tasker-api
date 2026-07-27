import enum
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4
from sqlmodel import Field, SQLModel
from app.core.utils.datetime_helper import lagos_now

class AdminRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    COMPLIANCE_OFFICER = "compliance_officer"
    SUPPORT_AGENT = "support_agent"

class AdminUser(SQLModel, table=True):
    __tablename__ = "admin_users"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    full_name: Optional[str] = None
    role: AdminRole
    is_active: bool = Field(default=True)
    region_id: Optional[str] = Field(default=None, foreign_key="regions.id", nullable=True, index=True)
    created_at: datetime = Field(default_factory=lagos_now)
    updated_at: datetime = Field(default_factory=lagos_now)
