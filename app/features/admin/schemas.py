from datetime import datetime
from typing import Optional, List, Dict
from pydantic import BaseModel, EmailStr, Field
from app.core.models.admins import AdminRole, AdminInvitationStatus
from app.features.regions.schemas import RegionResponse


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
    region_id: Optional[str] = None
    region: Optional[RegionResponse] = None
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


class AdminDashboardOverviewResponse(BaseModel):
    total_users: int = Field(description="Total registered users count")
    total_customers: int = Field(description="Total registered customers count")
    total_providers: int = Field(description="Total registered service providers count")
    total_tasks: int = Field(description="Total created tasks count")
    total_completed_tasks: int = Field(description="Total successfully completed tasks count")
    total_in_progress_tasks: int = Field(description="Total currently in-progress tasks count")
    total_open_tasks: int = Field(description="Total open, searching, or assigned tasks count")
    total_cancelled_tasks: int = Field(description="Total cancelled tasks count")
    total_revenue_amount: float = Field(description="Total revenue amount summed from transactions table")
    total_processed_payouts_amount: float = Field(description="Total processed payouts amount summed from payout queue table with status COMPLETED")


class AdminUserStatsResponse(BaseModel):
    total_users: int = Field(description="Total registered users count")
    total_active: int = Field(description="Total active users count")
    total_inactive: int = Field(description="Total inactive users count")
    total_customers: int = Field(description="Total customer users count")
    total_providers: int = Field(description="Total provider users count")


class AdminKYCStatsResponse(BaseModel):
    total_documents: int = Field(description="Total KYC document submissions count")
    total_verified: int = Field(description="Total verified KYC documents count")
    total_rejected: int = Field(description="Total failed/rejected KYC documents count")
    total_pending: int = Field(description="Total pending KYC documents count (submitted or under review)")
    total_submitted: int = Field(description="Total submitted KYC documents count")
    total_under_review: int = Field(description="Total under review KYC documents count")


class AdminGuarantorStatsResponse(BaseModel):
    total_guarantors: int = Field(description="Total guarantors count")
    total_passed: int = Field(description="Total passed/verified guarantors count")
    total_failed: int = Field(description="Total failed/rejected guarantors count")
    total_pending: int = Field(description="Total pending guarantors count")
    total_under_review: int = Field(description="Total under review guarantors count")


class AdminInterviewStatsResponse(BaseModel):
    total_interviews: int = Field(description="Total provider interviews count")
    total_scheduled: int = Field(description="Total scheduled interviews count")
    total_passed: int = Field(description="Total passed interviews count")
    total_failed: int = Field(description="Total failed interviews count")
    total_cancelled: int = Field(description="Total cancelled interviews count")
    total_rescheduled: int = Field(description="Total rescheduled interviews count")


class AdminTaskStatsResponse(BaseModel):
    total_tasks: int = Field(description="Total tasks count")
    total_draft: int = Field(default=0, description="Total draft tasks count")
    total_under_review: int = Field(default=0, description="Total under review tasks count")
    total_open: int = Field(default=0, description="Total open tasks count")
    total_searching: int = Field(default=0, description="Total searching tasks count")
    total_assigned: int = Field(default=0, description="Total assigned tasks count")
    total_in_progress: int = Field(default=0, description="Total in-progress tasks count")
    total_completed: int = Field(default=0, description="Total completed tasks count")
    total_cancelled: int = Field(default=0, description="Total cancelled tasks count")
    total_no_match: int = Field(default=0, description="Total no-match tasks count")
    by_status: Dict[str, int] = Field(default_factory=dict, description="Counts grouped by task status")


