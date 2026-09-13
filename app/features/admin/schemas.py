from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field
from app.core.models.admins import AdminRole, AdminInvitationStatus


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str


class AdminTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str
    admin: "AdminUserResponse"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class AcceptInvitationRequest(BaseModel):
    token: str
    password: str = Field(min_length=8)
    fullname: str


class AdminInviteRequest(BaseModel):
    email: EmailStr
    role: AdminRole


class ChangeRoleRequest(BaseModel):
    new_role: AdminRole


class AdminUserResponse(BaseModel):
    id: str
    email: str
    fullname: Optional[str] = None
    role: AdminRole
    parent_admin_id: Optional[str] = None
    created_by_id: Optional[str] = None
    is_active: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AdminInvitationResponse(BaseModel):
    id: str
    email: str
    role: AdminRole
    invited_by_id: str
    status: AdminInvitationStatus
    expires_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminAuditLogResponse(BaseModel):
    id: str
    admin_id: Optional[str] = None
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    meta_data: Optional[dict] = None
    reason: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
