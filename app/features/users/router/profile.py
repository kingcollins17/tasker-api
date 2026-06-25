from fastapi import HTTPException
from fastapi import APIRouter, Depends, status, Response
from app.core.api_response import BaseAPIResponse
from app.core.deps import get_current_user, GetCurrentUser
from app.core.models.users import UserType
from app.features.users.schemas import UserResponse, ProviderProfileUpdate, CustomerProfileUpdate, UpdateLocation, UpdateCloudMessagingToken
from app.features.users.services import UserService, get_user_service

router = APIRouter()


@router.get("/me", response_model=BaseAPIResponse[UserResponse], status_code=status.HTTP_200_OK)
async def get_me(
    current_user: UserResponse = Depends(get_current_user)
):
    """Retrieve the profile details of the currently authenticated user."""
    return BaseAPIResponse[UserResponse](
        data=current_user,
        detail="User profile retrieved successfully.",
        statusCode=status.HTTP_200_OK
    )


@router.put("/update-provider-profile", response_model=BaseAPIResponse[UserResponse], status_code=status.HTTP_200_OK)
async def update_profile(
    schema: ProviderProfileUpdate,
    response: Response,
    current_user: UserResponse = Depends(GetCurrentUser(required_type=UserType.PROVIDER)),
    user_service: UserService = Depends(get_user_service)
):
    """Update profile details for the currently authenticated provider."""
    try:
        updated_user = await user_service.update_provider_profile(
            user_id=current_user.id,
            first_name=schema.first_name,
            last_name=schema.last_name,
            gender=schema.gender,
            phone_number=schema.phone_number
        )
        return BaseAPIResponse[UserResponse](
            data=UserResponse.model_validate(updated_user),
            detail="Provider profile updated successfully.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        response.status_code = e.status_code
        return BaseAPIResponse[UserResponse](
            detail=e.detail,
            statusCode=e.status_code
        )
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return BaseAPIResponse[UserResponse](
            detail="An unexpected error occurred during profile update.",
            statusCode=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.put("/update-seeker-profile", response_model=BaseAPIResponse[UserResponse], status_code=status.HTTP_200_OK)
async def update_seeker_profile(
    schema: CustomerProfileUpdate,
    response: Response,
    current_user: UserResponse = Depends(GetCurrentUser(required_type=UserType.CUSTOMER)),
    user_service: UserService = Depends(get_user_service)
):
    """Update profile details for the currently authenticated seeker (customer)."""
    try:
        updated_user = await user_service.update_customer_profile(
            user_id=current_user.id,
            first_name=schema.first_name,
            last_name=schema.last_name
        )
        return BaseAPIResponse[UserResponse](
            data=UserResponse.model_validate(updated_user),
            detail="Seeker profile updated successfully.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        response.status_code = e.status_code
        return BaseAPIResponse[UserResponse](
            detail=e.detail,
            statusCode=e.status_code
        )
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return BaseAPIResponse[UserResponse](
            detail="An unexpected error occurred during profile update.",
            statusCode=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.put("/location", response_model=BaseAPIResponse[None], status_code=status.HTTP_200_OK)
async def update_location(
    schema: UpdateLocation,
    response: Response,
    current_user: UserResponse = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service)
):
    """Update the live last known location of the currently authenticated user (seeker or tasker)."""
    try:
        await user_service.update_user_location(
            user_id=current_user.id,
            user_type=current_user.type,
            latitude=schema.latitude,
            longitude=schema.longitude
        )
        return BaseAPIResponse[None](
            detail="Location updated successfully.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        response.status_code = e.status_code
        return BaseAPIResponse[None](
            detail=e.detail,
            statusCode=e.status_code
        )
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return BaseAPIResponse[None](
            detail="An unexpected error occurred while updating location.",
            statusCode=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.put("/cloud-messaging-token", response_model=BaseAPIResponse[None], status_code=status.HTTP_200_OK)
async def update_cloud_messaging_token(
    schema: UpdateCloudMessagingToken,
    response: Response,
    current_user: UserResponse = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service)
):
    """Update the cloud messaging token for push notifications."""
    try:
        await user_service.update_cloud_messaging_token(current_user.id, schema.token)
        return BaseAPIResponse[None](
            detail="Cloud messaging token updated successfully.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        response.status_code = e.status_code
        return BaseAPIResponse[None](
            detail=e.detail,
            statusCode=e.status_code
        )
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return BaseAPIResponse[None](
            detail="An unexpected error occurred while updating the token.",
            statusCode=status.HTTP_500_INTERNAL_SERVER_ERROR
        )



