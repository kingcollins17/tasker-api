from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlmodel import col, func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.database import get_session
from app.core.deps.auth import GetCurrentAdmin
from app.core.error_handler import AppErrorHandler
from app.core.models.admins import AdminUser
from app.core.models.users import CustomerProfile, ProviderProfile, User, UserType
from app.features.users.schemas import (
    AdminUserStatusUpdate,
    UserLiteResponse,
    UserResponse,
)
from app.features.users.services.user_management_service import (
    UserManagementService,
    get_user_management_service,
)

router = APIRouter(prefix="/admin", tags=["Admin - User Management"])


@router.get("", response_model=BaseAPIResponse[PaginatedData[UserLiteResponse]])
async def list_users(
    email: Optional[str] = Query(
        None, description="Search by user email (case-insensitive substring)"
    ),
    phone_number: Optional[str] = Query(
        None, description="Search by user phone number"
    ),
    name: Optional[str] = Query(None, description="Search by first name or last name"),
    user_type: Optional[UserType] = Query(
        None, alias="role", description="Filter by user role (CUSTOMER or PROVIDER)"
    ),
    is_active: Optional[bool] = Query(None, description="Filter by user active state"),
    region_id: Optional[str] = Query(None, description="Filter by region ID"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """List and search platform users with inlined retrieval queries, region filter, and pagination."""
    try:
        stmt = (
            select(User)
            .outerjoin(CustomerProfile, col(CustomerProfile.user_id) == col(User.id))
            .outerjoin(ProviderProfile, col(ProviderProfile.user_id) == col(User.id))
        )

        if email:
            stmt = stmt.where(col(User.email).ilike(f"%{email.strip()}%"))

        if phone_number:
            stmt = stmt.where(col(User.phone_number).contains(phone_number.strip()))

        if name:
            name_pattern = f"%{name.strip()}%"
            stmt = stmt.where(
                or_(
                    col(CustomerProfile.first_name).ilike(name_pattern),
                    col(CustomerProfile.last_name).ilike(name_pattern),
                    col(ProviderProfile.first_name).ilike(name_pattern),
                    col(ProviderProfile.last_name).ilike(name_pattern),
                )
            )

        if user_type:
            stmt = stmt.where(col(User.type) == user_type)

        if is_active is not None:
            stmt = stmt.where(col(User.is_active) == is_active)

        if region_id:
            stmt = stmt.where(col(User.region_id) == region_id)

        stmt = (
            stmt.order_by(col(User.created_at).desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        users = (await session.exec(stmt)).all()

        items = [UserLiteResponse.from_user(u) for u in users]
        paginated_data = PaginatedData[UserLiteResponse](
            items=items,
            total=len(items),
            page=page,
            per_page=per_page,
        )
        return BaseAPIResponse.success_response(
            data=paginated_data,
            message="Platform users retrieved successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred querying platform users.",
        )


@router.get("/{user_id}", response_model=BaseAPIResponse[UserResponse])
async def get_user_detail(
    user_id: str,
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """Fetch detailed information for a single platform user by ID using an inlined query."""
    try:
        stmt = select(User).where(col(User.id) == user_id)
        user = (await session.exec(stmt)).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            )

        return BaseAPIResponse.success_response(
            data=UserResponse.model_validate(user),
            message="User profile retrieved successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred fetching user details.",
        )


@router.post("/{user_id}/activate", response_model=BaseAPIResponse[UserResponse])
@router.patch("/{user_id}/activate", response_model=BaseAPIResponse[UserResponse])
async def activate_user(
    user_id: str,
    request: Request,
    body: Optional[AdminUserStatusUpdate] = None,
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    user_mgmt_service: UserManagementService = Depends(get_user_management_service),
):
    """Activate a platform user account with optional reason and metadata notes via write service."""
    try:
        ip_address = request.client.host if request and request.client else None
        user_agent = request.headers.get("user-agent") if request else None

        reason = body.reason if body else None
        meta_data = body.meta_data if body else None

        user = await user_mgmt_service.set_user_active_status(
            user_id=user_id,
            is_active=True,
            admin_id=current_admin.id,
            reason=reason,
            meta_data=meta_data,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return BaseAPIResponse.success_response(
            data=UserResponse.model_validate(user),
            message="User account activated successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred activating user account.",
        )


@router.post("/{user_id}/deactivate", response_model=BaseAPIResponse[UserResponse])
@router.patch("/{user_id}/deactivate", response_model=BaseAPIResponse[UserResponse])
async def deactivate_user(
    user_id: str,
    request: Request,
    body: Optional[AdminUserStatusUpdate] = None,
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    user_mgmt_service: UserManagementService = Depends(get_user_management_service),
):
    """Deactivate a platform user account with optional reason and metadata notes via write service."""
    try:
        ip_address = request.client.host if request and request.client else None
        user_agent = request.headers.get("user-agent") if request else None

        reason = body.reason if body else None
        meta_data = body.meta_data if body else None

        user = await user_mgmt_service.set_user_active_status(
            user_id=user_id,
            is_active=False,
            admin_id=current_admin.id,
            reason=reason,
            meta_data=meta_data,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return BaseAPIResponse.success_response(
            data=UserResponse.model_validate(user),
            message="User account deactivated successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred deactivating user account.",
        )
