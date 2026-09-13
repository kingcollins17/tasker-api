from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.deps.auth import GetCurrentAdmin
from app.core.error_handler import AppErrorHandler
from app.core.models.admins import AdminAuditLog, AdminRole, AdminUser
from app.features.admin.schemas import AdminAuditLogResponse

router = APIRouter(prefix="/audit", tags=["Admin Audit"])


@router.get("/logs", response_model=List[AdminAuditLogResponse])
async def list_audit_logs(
    admin_id: Optional[UUID] = Query(None),
    resource_type: Optional[str] = Query(None),
    resource_id: Optional[UUID] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """Query immutable admin audit logs."""
    try:
        stmt = select(AdminAuditLog)

        if admin_id:
            stmt = stmt.where(col(AdminAuditLog.admin_id) == admin_id)
        if resource_type:
            stmt = stmt.where(col(AdminAuditLog.resource_type) == resource_type)
        if resource_id:
            stmt = stmt.where(col(AdminAuditLog.resource_id) == resource_id)
        if action:
            stmt = stmt.where(col(AdminAuditLog.action) == action)

        stmt = stmt.order_by(col(AdminAuditLog.created_at).desc()).limit(limit).offset(offset)
        logs = (await session.exec(stmt)).all()

        return [AdminAuditLogResponse.model_validate(log) for log in logs]
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred querying audit logs.",
        )
