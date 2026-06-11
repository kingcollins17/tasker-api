import enum
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4
from sqlmodel import Field, SQLModel

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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
