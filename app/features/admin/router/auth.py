from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.api_response import BaseAPIResponse
from app.core.deps.auth import GetCurrentAdmin
from app.core.error_handler import AppErrorHandler
from app.core.models.admins import AdminUser
from app.core.utils import security
from app.features.admin.schemas import (
    AcceptInvitationRequest,
    AdminLoginRequest,
    AdminTokenResponse,
    AdminUserResponse,
    RefreshTokenRequest,
)
from app.features.admin.services import (
    AdminService,
    AuditService,
    get_admin_service,
    get_audit_service,
)

router = APIRouter(prefix="/auth", tags=["Admin Auth"])


@router.post("/login", response_model=BaseAPIResponse[AdminTokenResponse])
async def login(
    body: AdminLoginRequest,
    request: Request,
    admin_service: AdminService = Depends(get_admin_service),
):
    """Authenticate administrator credentials and return JWT access and refresh tokens."""
    try:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        admin, token_data = await admin_service.login(
            email=body.email,
            password=body.password,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        token_response = AdminTokenResponse(
            access_token=token_data["access_token"],
            token_type="bearer",
            refresh_token=token_data["refresh_token"],
            admin=AdminUserResponse.model_validate(admin),
        )
        return BaseAPIResponse.success_response(
            data=token_response,
            message="Admin logged in successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred processing the login request.",
        )


@router.post("/refresh", response_model=BaseAPIResponse[AdminTokenResponse])
async def refresh_token(
    body: RefreshTokenRequest,
    admin_service: AdminService = Depends(get_admin_service),
):
    """Issue a new access token using a valid admin refresh token."""
    try:
        payload = security.decode_access_token(body.refresh_token)
        if not payload or payload.get("type") != "admin_refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token",
            )

        admin_id = payload.get("id")
        admin = await admin_service.admin_repo.get(admin_id)
        if not admin or not admin.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Administrator account is inactive or not found",
            )

        new_payload = {"id": admin.id, "type": "admin", "role": admin.role.value}
        access_token = security.create_access_token(new_payload)

        token_response = AdminTokenResponse(
            access_token=access_token,
            token_type="bearer",
            refresh_token=body.refresh_token,
            admin=AdminUserResponse.model_validate(admin),
        )
        return BaseAPIResponse.success_response(
            data=token_response,
            message="Token refreshed successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred refreshing the access token.",
        )


@router.post("/logout", response_model=BaseAPIResponse[None])
async def logout(
    request: Request,
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    audit_service: AuditService = Depends(get_audit_service),
):
    """Logout current administrator and log audit event."""
    try:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        await audit_service.log_audit(
            action="LOGOUT",
            resource_type="AdminUser",
            admin_id=current_admin.id,
            resource_id=current_admin.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return BaseAPIResponse.success_response(message="Logged out successfully.")
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred processing logout.",
        )


@router.post("/accept-invitation", response_model=BaseAPIResponse[AdminUserResponse])
async def accept_invitation(
    body: AcceptInvitationRequest,
    request: Request,
    admin_service: AdminService = Depends(get_admin_service),
):
    """Consume an invitation token to create an active administrator account."""
    try:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        admin = await admin_service.accept_invitation(
            token=body.token,
            password=body.password,
            fullname=body.fullname,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return BaseAPIResponse.success_response(
            data=AdminUserResponse.model_validate(admin),
            message="Invitation accepted successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred accepting the invitation.",
        )
