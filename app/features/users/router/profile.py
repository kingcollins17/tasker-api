import inspect
from fastapi import HTTPException
from fastapi import APIRouter, Depends, status, Response
from sqlmodel import select
from app.core.api_response import BaseAPIResponse
from app.core.deps import get_current_user, GetCurrentUser
from app.core.models.users import UserType
from app.core.models.services import Service, ProviderServiceLink
from app.features.users.schemas import UserResponse, ProviderProfileUpdate, CustomerProfileUpdate, UpdateLocation, UpdateCloudMessagingToken, AttachProviderService, ServiceResponse
from app.features.users.services import UserService, get_user_service

router = APIRouter()


@router.get("/me", response_model=BaseAPIResponse[UserResponse], status_code=status.HTTP_200_OK)
async def get_me(
    response: Response,
    current_user: UserResponse = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service)
):
    """Retrieve the profile details of the currently authenticated user."""
    try:
        if current_user.type == UserType.PROVIDER and current_user.provider_profile:
            def _get_services(current_user, user_service):
                stmt = select(Service).join(
                    ProviderServiceLink, Service.id == ProviderServiceLink.service_id  # type: ignore
                ).where(
                    ProviderServiceLink.provider_id == current_user.id
                ).where(
                    Service.is_active == True
                )

                result = user_service.provider_repo.execute(stmt)
                return result

            result = _get_services(current_user, user_service)

            if inspect.isawaitable(result):
                result = await result
                services = list(result.all())
            else:
                services = []

            current_user.provider_profile.services = [
                ServiceResponse.model_validate(s) for s in services
            ]

        return BaseAPIResponse[UserResponse](
            data=current_user,
            detail="User profile retrieved successfully.",
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
            detail=f"An unexpected error occurred: {str(e)}",
            statusCode=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.put("/update-provider-profile", response_model=BaseAPIResponse[UserResponse], status_code=status.HTTP_200_OK)
async def update_profile(
    schema: ProviderProfileUpdate,
    response: Response,
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)),
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
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.CUSTOMER)),
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
            longitude=schema.longitude,
            address_line=schema.address_line
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


@router.post("/provider/services", response_model=BaseAPIResponse[None], status_code=status.HTTP_200_OK)
async def attach_provider_service(
    schema: AttachProviderService,
    response: Response,
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)),
    user_service: UserService = Depends(get_user_service)
):
    """Add a service to the authenticated provider's account. Max 3 services allowed."""
    try:
        await user_service.attach_provider_service(
            user_id=current_user.id,
            service_id=schema.service_id
        )
        return BaseAPIResponse[None](
            detail="Service successfully added.",
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
            detail=f"An unexpected error occurred: {str(e)}",
            statusCode=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.delete("/provider/services/{service_id}", response_model=BaseAPIResponse[None], status_code=status.HTTP_200_OK)
async def remove_provider_service(
    service_id: str,
    response: Response,
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)),
    user_service: UserService = Depends(get_user_service)
):
    """Remove a service from the authenticated provider's account."""
    try:
        await user_service.remove_provider_service(
            user_id=current_user.id,
            service_id=service_id
        )
        return BaseAPIResponse[None](
            detail="Service successfully removed.",
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
            detail=f"An unexpected error occurred: {str(e)}",
            statusCode=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
