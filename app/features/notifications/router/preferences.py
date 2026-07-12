from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.api_response import BaseAPIResponse
from app.core.deps import GetCurrentUser
from app.core.error_handler import AppErrorHandler
from app.features.notifications.schemas import (
    BulkUpdatePreferences,
    NotificationPreferenceResponse,
)
from app.features.notifications.services import (
    NotificationService,
    get_notification_service,
)
from app.features.users.schemas import UserResponse

router = APIRouter()


@router.get(
    "/",
    response_model=BaseAPIResponse[List[NotificationPreferenceResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_preferences(
    current_user: UserResponse = Depends(GetCurrentUser()),
    service: NotificationService = Depends(get_notification_service),
):
    """Get the current user's notification preferences."""
    try:
        preferences = await service.get_preferences(user_id=current_user.id)
        return BaseAPIResponse[List[NotificationPreferenceResponse]](
            data=[
                NotificationPreferenceResponse.model_validate(p)
                for p in preferences
            ],
            detail="Notification preferences retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving preferences.",
        )


@router.put(
    "/",
    response_model=BaseAPIResponse[List[NotificationPreferenceResponse]],
    status_code=status.HTTP_200_OK,
)
async def update_preferences(
    schema: BulkUpdatePreferences,
    current_user: UserResponse = Depends(GetCurrentUser()),
    service: NotificationService = Depends(get_notification_service),
):
    """Bulk upsert notification preferences for the current user."""
    try:
        updated = await service.update_preferences(
            user_id=current_user.id,
            schema=schema,
        )
        return BaseAPIResponse[List[NotificationPreferenceResponse]](
            data=[
                NotificationPreferenceResponse.model_validate(p)
                for p in updated
            ],
            detail="Notification preferences updated successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating preferences.",
        )
