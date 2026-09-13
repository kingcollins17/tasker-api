from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

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


@router.post("/invite")
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
        return response_data
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred creating the invitation.",
        )


@router.get("/invitations", response_model=List[AdminInvitationResponse])
async def list_invitations(
    invitation_status: Optional[AdminInvitationStatus] = Query(None, alias="status"),
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """List admin invitations."""
    try:
        stmt = select(AdminInvitation)
        if invitation_status:
            stmt = stmt.where(col(AdminInvitation.status) == invitation_status)

        # Root admin can see all; others see invitations created by them or in hierarchy
        if current_admin.role != AdminRole.ROOT_ADMIN:
            stmt = stmt.where(col(AdminInvitation.invited_by_id) == current_admin.id)

        stmt = stmt.order_by(col(AdminInvitation.created_at).desc())
        invitations = (await session.exec(stmt)).all()
        return [AdminInvitationResponse.model_validate(inv) for inv in invitations]
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred retrieving invitations.",
        )


@router.post("/invitations/{invitation_id}/resend")
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
        return response_data
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred resending the invitation.",
        )


@router.post("/invitations/{invitation_id}/revoke", response_model=AdminInvitationResponse)
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
        return AdminInvitationResponse.model_validate(invitation)
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred revoking the invitation.",
        )


@router.get("", response_model=List[AdminUserResponse])
async def list_admins(
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
    admin_service: AdminService = Depends(get_admin_service),
):
    """List administrators. Root Admin sees all; Super Admins see self and descendants."""
    try:
        stmt = select(AdminUser).order_by(col(AdminUser.created_at).desc())
        all_admins = (await session.exec(stmt)).all()

        if current_admin.role == AdminRole.ROOT_ADMIN:
            return [AdminUserResponse.model_validate(adm) for adm in all_admins]

        # Filter descendants
        visible = []
        for adm in all_admins:
            if adm.id == current_admin.id or await admin_service.is_descendant(current_admin.id, adm.id):
                visible.append(adm)

        return [AdminUserResponse.model_validate(adm) for adm in visible]
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred listing administrators.",
        )


@router.get("/{admin_id}", response_model=AdminUserResponse)
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

        return AdminUserResponse.model_validate(target)
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred fetching administrator details.",
        )


@router.patch("/{admin_id}/role", response_model=AdminUserResponse)
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
        return AdminUserResponse.model_validate(target)
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred updating administrator role.",
        )


@router.post("/{admin_id}/deactivate", response_model=AdminUserResponse)
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
        return AdminUserResponse.model_validate(target)
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred deactivating administrator.",
        )


@router.post("/{admin_id}/reactivate", response_model=AdminUserResponse)
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
        return AdminUserResponse.model_validate(target)
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred reactivating administrator.",
        )
