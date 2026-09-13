from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.database import get_session
from app.core.deps.auth import GetCurrentAdmin
from app.core.error_handler import AppErrorHandler
from app.core.models.admins import AdminInvitation, AdminInvitationStatus, AdminRole, AdminUser
from app.features.admin.schemas import (
    AdminInviteRequest,
    AdminInvitationResponse,
    AdminUserResponse,
    ChangeRoleRequest,
)
from app.features.admin.services import AdminService, get_admin_service

router = APIRouter(prefix="/users", tags=["Admin Management"])


@router.post("/invite", response_model=BaseAPIResponse[Dict[str, Any]])
async def invite_admin(
    body: AdminInviteRequest,
    request: Request,
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    admin_service: AdminService = Depends(get_admin_service),
):
    """Invite a new administrator account."""
    try:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        invitation, raw_token = await admin_service.create_invitation(
            requester=current_admin,
            email=body.email,
            role=body.role,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        response_data = AdminInvitationResponse.model_validate(invitation).model_dump()
        response_data["invitation_token"] = raw_token
        return BaseAPIResponse.success_response(
            data=response_data,
            message="Invitation created successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred creating the invitation.",
        )


@router.get("/invitations", response_model=BaseAPIResponse[PaginatedData[AdminInvitationResponse]])
async def list_invitations(
    invitation_status: Optional[AdminInvitationStatus] = Query(None, alias="status"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """List admin invitations with pagination."""
    try:
        stmt = select(AdminInvitation)
        if invitation_status:
            stmt = stmt.where(col(AdminInvitation.status) == invitation_status)

        # Root admin can see all; others see invitations created by them or in hierarchy
        if current_admin.role != AdminRole.ROOT_ADMIN:
            stmt = stmt.where(col(AdminInvitation.invited_by_id) == current_admin.id)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.exec(count_stmt)).one()

        stmt = stmt.order_by(col(AdminInvitation.created_at).desc()).offset((page - 1) * per_page).limit(per_page)
        invitations = (await session.exec(stmt)).all()

        items = [AdminInvitationResponse.model_validate(inv) for inv in invitations]
        paginated_data = PaginatedData[AdminInvitationResponse](
            items=items,
            total=total,
            page=page,
            per_page=per_page,
        )
        return BaseAPIResponse.success_response(
            data=paginated_data,
            message="Admin invitations retrieved successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred retrieving invitations.",
        )


@router.post("/invitations/{invitation_id}/resend", response_model=BaseAPIResponse[Dict[str, Any]])
async def resend_invitation(
    invitation_id: str,
    request: Request,
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    admin_service: AdminService = Depends(get_admin_service),
):
    """Resend an admin invitation by issuing a new token."""
    try:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        new_inv, raw_token = await admin_service.resend_invitation(
            requester=current_admin,
            invitation_id=invitation_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        response_data = AdminInvitationResponse.model_validate(new_inv).model_dump()
        response_data["invitation_token"] = raw_token
        return BaseAPIResponse.success_response(
            data=response_data,
            message="Invitation resent successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred resending the invitation.",
        )


@router.post("/invitations/{invitation_id}/revoke", response_model=BaseAPIResponse[AdminInvitationResponse])
async def revoke_invitation(
    invitation_id: str,
    request: Request,
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    admin_service: AdminService = Depends(get_admin_service),
):
    """Revoke an active admin invitation."""
    try:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        invitation = await admin_service.revoke_invitation(
            requester=current_admin,
            invitation_id=invitation_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return BaseAPIResponse.success_response(
            data=AdminInvitationResponse.model_validate(invitation),
            message="Invitation revoked successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred revoking the invitation.",
        )


@router.get("", response_model=BaseAPIResponse[PaginatedData[AdminUserResponse]])
async def list_admins(
    email: Optional[str] = Query(None, description="Search by email (case-insensitive substring)"),
    fullname: Optional[str] = Query(None, description="Search by full name (case-insensitive substring)"),
    role: Optional[AdminRole] = Query(None, description="Filter by admin role"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    region_id: Optional[str] = Query(None, description="Filter by region ID"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
    admin_service: AdminService = Depends(get_admin_service),
):
    """List administrators with filtering by email, fullname, role, active status, region_id, and hierarchy permissions."""
    try:
        stmt = select(AdminUser)

        if email:
            stmt = stmt.where(col(AdminUser.email).ilike(f"%{email.strip()}%"))

        if fullname:
            stmt = stmt.where(col(AdminUser.fullname).ilike(f"%{fullname.strip()}%"))

        if role:
            stmt = stmt.where(col(AdminUser.role) == role)

        if is_active is not None:
            stmt = stmt.where(col(AdminUser.is_active) == is_active)

        if region_id:
            stmt = stmt.where(col(AdminUser.region_id) == region_id)

        stmt = stmt.order_by(col(AdminUser.created_at).desc())

        if current_admin.role == AdminRole.ROOT_ADMIN:
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.exec(count_stmt)).one()

            stmt = stmt.offset((page - 1) * per_page).limit(per_page)
            paged_admins = (await session.exec(stmt)).all()
        else:
            all_matching = (await session.exec(stmt)).all()
            visible = []
            for adm in all_matching:
                if adm.id == current_admin.id or await admin_service.is_descendant(current_admin.id, adm.id):
                    visible.append(adm)

            total = len(visible)
            offset = (page - 1) * per_page
            paged_admins = visible[offset: offset + per_page]

        items = [AdminUserResponse.model_validate(adm) for adm in paged_admins]
        paginated_data = PaginatedData[AdminUserResponse](
            items=items,
            total=total,
            page=page,
            per_page=per_page,
        )
        return BaseAPIResponse.success_response(
            data=paginated_data,
            message="Administrators retrieved successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred listing administrators.",
        )


@router.get("/{admin_id}", response_model=BaseAPIResponse[AdminUserResponse])
async def get_admin_detail(
    admin_id: str,
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    admin_service: AdminService = Depends(get_admin_service),
):
    """Fetch administrator detail by ID."""
    try:
        target = await admin_service.admin_repo.get(admin_id)
        if not target:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Administrator not found")

        if current_admin.id != target.id and not await admin_service.can_manage_admin(current_admin, target):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this administrator")

        return BaseAPIResponse.success_response(
            data=AdminUserResponse.model_validate(target),
            message="Administrator details retrieved successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred fetching administrator details.",
        )


@router.patch("/{admin_id}/role", response_model=BaseAPIResponse[AdminUserResponse])
async def change_admin_role(
    admin_id: str,
    body: ChangeRoleRequest,
    request: Request,
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    admin_service: AdminService = Depends(get_admin_service),
):
    """Promote or demote an administrator's role."""
    try:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        target = await admin_service.change_admin_role(
            requester=current_admin,
            target_admin_id=admin_id,
            new_role=body.new_role,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return BaseAPIResponse.success_response(
            data=AdminUserResponse.model_validate(target),
            message="Administrator role updated successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred updating administrator role.",
        )


@router.post("/{admin_id}/deactivate", response_model=BaseAPIResponse[AdminUserResponse])
async def deactivate_admin(
    admin_id: str,
    request: Request,
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    admin_service: AdminService = Depends(get_admin_service),
):
    """Deactivate an administrator account."""
    try:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        target = await admin_service.deactivate_admin(
            requester=current_admin,
            target_admin_id=admin_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return BaseAPIResponse.success_response(
            data=AdminUserResponse.model_validate(target),
            message="Administrator deactivated successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred deactivating administrator.",
        )


@router.post("/{admin_id}/reactivate", response_model=BaseAPIResponse[AdminUserResponse])
async def reactivate_admin(
    admin_id: str,
    request: Request,
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    admin_service: AdminService = Depends(get_admin_service),
):
    """Reactivate a deactivated administrator account."""
    try:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        target = await admin_service.reactivate_admin(
            requester=current_admin,
            target_admin_id=admin_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return BaseAPIResponse.success_response(
            data=AdminUserResponse.model_validate(target),
            message="Administrator reactivated successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred reactivating administrator.",
        )
