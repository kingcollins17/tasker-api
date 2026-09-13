from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.database import get_session
from app.core.deps.auth import GetCurrentAdmin
from app.core.error_handler import AppErrorHandler
from app.core.models.admins import AdminAuditLog, AdminRole, AdminUser
from app.features.admin.schemas import AdminAuditLogResponse

router = APIRouter(prefix="/audit", tags=["Admin Audit"])


@router.get("/logs", response_model=BaseAPIResponse[PaginatedData[AdminAuditLogResponse]])
async def list_audit_logs(
    admin_id: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    resource_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(50, ge=1, le=500, description="Items per page"),
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """Query immutable admin audit logs with pagination."""
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

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.exec(count_stmt)).one()

        stmt = stmt.order_by(col(AdminAuditLog.created_at).desc()).offset((page - 1) * per_page).limit(per_page)
        logs = (await session.exec(stmt)).all()

        items = [AdminAuditLogResponse.model_validate(log) for log in logs]
        paginated_data = PaginatedData[AdminAuditLogResponse](
            items=items,
            total=total,
            page=page,
            per_page=per_page,
        )
        return BaseAPIResponse.success_response(
            data=paginated_data,
            message="Audit logs retrieved successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred querying audit logs.",
        )
