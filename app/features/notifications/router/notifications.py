from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.deps import GetCurrentUser
from app.core.error_handler import AppErrorHandler
from app.core.services.connection_manager import get_connection_manager
from app.core.utils import security
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
    current_user: UserResponse = Depends(GetCurrentUser()),
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
    current_user: UserResponse = Depends(GetCurrentUser()),
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
    current_user: UserResponse = Depends(GetCurrentUser()),
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


@router.websocket("/ws")
async def notifications_websocket(
    websocket: WebSocket,
    token: str = Query(default=None),
):
    """WebSocket endpoint for real-time in-app notifications.

    Clients connect with their JWT token as a query parameter:
        ws://<host>/api/v1/notifications/ws?token=<jwt>

    On successful authentication the connection is held open. The server
    pushes notification payloads as JSON messages whenever a new in-app
    notification is created for the authenticated user.
    """
    # Authenticate via JWT query parameter
    if not token:
        await websocket.close(code=4001, reason="Missing authentication token")
        return

    payload = security.decode_access_token(token)
    if not payload:
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    user_id = payload.get("id")
    if not user_id:
        await websocket.close(code=4001, reason="Invalid token payload")
        return

    manager = get_connection_manager()
    await manager.connect(user_id, websocket)

    try:
        # Keep the connection alive by waiting for incoming messages.
        # Clients can send pings or any text; the server just reads to
        # detect disconnection.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)
    except Exception:
        manager.disconnect(user_id, websocket)

