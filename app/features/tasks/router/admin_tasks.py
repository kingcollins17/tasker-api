from typing import List, Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.database import get_session
from app.core.deps import GetCurrentAdmin
from app.core.error_handler import AppErrorHandler
from app.core.models.admins import AdminUser
from app.core.models.tasks import (
    DispatchAttemptStatus,
    DispatchSession,
    DispatchSessionStatus,
    DispatchSessionTrigger,
    TaskDispatchAttempt,
)
from app.core.models.users import ProviderProfile, User
from app.core.schemas.tasks import (
    DispatchAttemptProviderResponse,
    DispatchSessionResponse,
    TaskDispatchAttemptResponse,
    TaskResponse,
)
from app.features.tasks.dispatch_service import DispatchService, get_dispatch_service
from app.features.tasks.schemas import TaskCancellationRequest, TaskRedispatchRequest
from app.features.tasks.task_service import TaskService, get_task_service

router = APIRouter(prefix="/admin/tasks", tags=["Admin Tasks"])


@router.get(
    "/dispatch-sessions",
    response_model=BaseAPIResponse[PaginatedData[DispatchSessionResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_dispatch_sessions(
    task_id: Optional[str] = Query(None, description="Filter dispatch sessions by task ID"),
    trigger: Optional[DispatchSessionTrigger] = Query(None, description="Filter by trigger type"),
    status_filter: Optional[DispatchSessionStatus] = Query(None, alias="status", description="Filter by session status"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """List dispatch sessions with optional query parameter filtering. Inlined SQLModel query."""
    try:
        stmt = select(DispatchSession)

        if task_id:
            stmt = stmt.where(col(DispatchSession.task_id) == task_id)

        if trigger:
            stmt = stmt.where(col(DispatchSession.trigger) == trigger)

        if status_filter:
            stmt = stmt.where(col(DispatchSession.status) == status_filter)

        stmt = (
            stmt.order_by(col(DispatchSession.created_at).desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        res = await session.exec(stmt)
        items = res.all()

        paginated = PaginatedData[DispatchSessionResponse](
            items=[DispatchSessionResponse.model_validate(s) for s in items],
            total=None,
            page=page,
            per_page=per_page,
        )

        return BaseAPIResponse.success_response(
            data=paginated,
            message="Dispatch sessions retrieved successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve dispatch sessions",
        )


@router.get(
    "/dispatch-attempts",
    response_model=BaseAPIResponse[PaginatedData[TaskDispatchAttemptResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_dispatch_attempts(
    task_id: Optional[str] = Query(None, description="Filter dispatch attempts by task ID"),
    dispatch_session_id: Optional[str] = Query(None, description="Filter by dispatch session ID"),
    provider_id: Optional[str] = Query(None, description="Filter by provider ID"),
    status_filter: Optional[DispatchAttemptStatus] = Query(None, alias="status", description="Filter by attempt status"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """List dispatch attempts for a task or dispatch session with query filtering and provider details. Inlined SQLModel query."""
    try:
        stmt = (
            select(
                TaskDispatchAttempt,
                col(ProviderProfile.first_name),
                col(ProviderProfile.last_name),
                col(User.email),
                col(User.phone_number),
            )
            .join(
                ProviderProfile,
                col(TaskDispatchAttempt.provider_id) == col(ProviderProfile.user_id),
                isouter=True,
            )
            .join(
                User,
                col(TaskDispatchAttempt.provider_id) == col(User.id),
                isouter=True,
            )
        )

        if task_id:
            stmt = stmt.where(col(TaskDispatchAttempt.task_id) == task_id)

        if dispatch_session_id:
            stmt = stmt.where(col(TaskDispatchAttempt.dispatch_session_id) == dispatch_session_id)

        if provider_id:
            stmt = stmt.where(col(TaskDispatchAttempt.provider_id) == provider_id)

        if status_filter:
            stmt = stmt.where(col(TaskDispatchAttempt.status) == status_filter)

        stmt = (
            stmt.order_by(col(TaskDispatchAttempt.pinged_at).desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        res = await session.exec(stmt)
        rows = res.all()

        items: List[TaskDispatchAttemptResponse] = []
        for row in rows:
            attempt, first_name, last_name, email, phone_number = row
            attempt_resp = TaskDispatchAttemptResponse.model_validate(attempt)
            if attempt.provider_id or first_name or last_name or email or phone_number:
                attempt_resp.provider = DispatchAttemptProviderResponse(
                    id=attempt.provider_id,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    phone_number=phone_number,
                )
            items.append(attempt_resp)

        paginated = PaginatedData[TaskDispatchAttemptResponse](
            items=items,
            total=None,
            page=page,
            per_page=per_page,
        )

        return BaseAPIResponse.success_response(
            data=paginated,
            message="Dispatch attempts retrieved successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve dispatch attempts",
        )


@router.post(
    "/{task_id}/redispatch",
    response_model=BaseAPIResponse[TaskResponse],
    status_code=status.HTTP_200_OK,
)
async def trigger_admin_redispatch(
    task_id: str,
    body: TaskRedispatchRequest = Body(default=TaskRedispatchRequest()),
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    dispatch_service: DispatchService = Depends(get_dispatch_service),
):
    """Trigger admin-initiated redispatch for a task."""
    try:
        task = await dispatch_service.manual_redispatch(
            task_id=task_id,
            current_user_id=current_admin.id,
            feedback=body.feedback,
            is_admin=True,
        )
        return BaseAPIResponse.success_response(
            data=TaskResponse.model_validate(task),
            message="Task redispatch triggered successfully by admin",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to trigger admin redispatch for task",
        )


@router.post(
    "/{task_id}/cancel",
    response_model=BaseAPIResponse[TaskResponse],
    status_code=status.HTTP_200_OK,
)
async def admin_cancel_task(
    task_id: str,
    body: TaskCancellationRequest = Body(default=TaskCancellationRequest()),
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    task_service: TaskService = Depends(get_task_service),
):
    """Cancel a task by an administrator."""
    try:
        task = await task_service.cancel_task(
            task_id=task_id,
            current_user_id=current_admin.id,
            cancellation_reason=body.cancellation_reason,
            is_admin=True,
        )
        return BaseAPIResponse.success_response(
            data=TaskResponse.model_validate(task),
            message="Task cancelled successfully by admin",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel task by admin",
        )
