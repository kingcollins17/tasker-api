from fastapi import APIRouter, Depends, HTTPException, status

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.deps import get_current_user
from app.core.error_handler import AppErrorHandler
from app.features.notifications.schemas import (
    MarkReadRequest,
    NotificationCountsResponse,
    UserNotificationResponse,
)
from app.features.notifications.services import (
    NotificationService,
    get_notification_service,
)
from app.features.users.schemas import UserResponse

router = APIRouter()


@router.get(
    "/",
    response_model=BaseAPIResponse[PaginatedData[UserNotificationResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_notifications(
    page: int = 1,
    per_page: int = 20,
    current_user: UserResponse = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
):
    """List the current user's notifications with pagination."""
    try:
        offset = (page - 1) * per_page
        items, total = await service.get_user_notifications(
            user_id=current_user.id,
            limit=per_page,
            offset=offset,
        )
        data = PaginatedData[UserNotificationResponse](
            items=[UserNotificationResponse(**item) for item in items],
            total=total,
            page=page,
            per_page=per_page,
        )
        return BaseAPIResponse[PaginatedData[UserNotificationResponse]](
            data=data,
            detail="Notifications retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving notifications.",
        )


@router.get(
    "/counts",
    response_model=BaseAPIResponse[NotificationCountsResponse],
    status_code=status.HTTP_200_OK,
)
async def get_notification_counts(
    current_user: UserResponse = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
):
    """Get the number of read and unread notifications for the current user."""
    try:
        counts = await service.get_notification_counts(user_id=current_user.id)
        return BaseAPIResponse[NotificationCountsResponse](
            data=NotificationCountsResponse(
                read=counts["read"],
                unread=counts["unread"],
            ),
            detail="Notification counts retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving notification counts.",
        )


@router.post(
    "/mark-read",
    response_model=BaseAPIResponse[dict],
    status_code=status.HTTP_200_OK,
)
async def mark_as_read(
    schema: MarkReadRequest,
    current_user: UserResponse = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
):
    """Mark one or more notifications as read for the current user."""
    try:
        updated_count = await service.mark_as_read(
            user_id=current_user.id,
            notification_ids=schema.notification_ids,
        )
        return BaseAPIResponse[dict](
            data={"marked_read": updated_count},
            detail=f"{updated_count} notification(s) marked as read.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while marking notifications as read.",
        )
