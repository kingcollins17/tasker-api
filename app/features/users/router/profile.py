from app.core.utils.timer import Timer
from app.core.services.logger_service import LoggerService, get_logger_service
import inspect
from fastapi import HTTPException
from fastapi import APIRouter, Depends, status, Response
from app.core.error_handler import AppErrorHandler
from sqlmodel import select
from app.core.api_response import BaseAPIResponse
from app.core.deps import GetCurrentUser
from app.core.models.users import UserType
from app.core.models.services import Service, ProviderServiceLink
from app.features.users.schemas import (
    UserResponse,
    ProviderProfileUpdate,
    CustomerProfileUpdate,
    UpdateLocation,
    UpdateCloudMessagingToken,
    AttachProviderService,
    ServiceResponse,
    UpdateRegion,
    UpdateOnlineStatus,
    LocationPing,
)
from app.features.users.services import UserService, get_user_service

router = APIRouter()


@router.get("/me", response_model=BaseAPIResponse[UserResponse], status_code=status.HTTP_200_OK)
async def get_me(
    response: Response,
    current_user: UserResponse = Depends(GetCurrentUser()),
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Retrieve the profile details of the currently authenticated user."""
    try:
        timer = Timer()
        timer.start()
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

        await system_logger.metric('get_me', timer.stop(), source='profile.get_me')
        return BaseAPIResponse[UserResponse](
            data=current_user,
            detail="User profile retrieved successfully.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        await system_logger.warn('get_me failed', source='profile.get_me', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'get_me error: {str(e)}', source='profile.get_me')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}"
        )


@router.put("/update-provider-profile", response_model=BaseAPIResponse[UserResponse], status_code=status.HTTP_200_OK)
async def update_profile(
    schema: ProviderProfileUpdate,
    response: Response,
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)),
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Update profile details for the currently authenticated provider."""
    try:
        timer = Timer()
        timer.start()
        updated_user = await user_service.update_provider_profile(
            user_id=current_user.id,
            first_name=schema.first_name,
            last_name=schema.last_name,
            gender=schema.gender,
            phone_number=schema.phone_number
        )
        await system_logger.metric('update_profile', timer.stop(), source='profile.update_profile')
        return BaseAPIResponse[UserResponse](
            data=UserResponse.model_validate(updated_user),
            detail="Provider profile updated successfully.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        await system_logger.warn('update_profile failed', source='profile.update_profile', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'update_profile error: {str(e)}', source='profile.update_profile')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during profile update."
        )


@router.put("/update-seeker-profile", response_model=BaseAPIResponse[UserResponse], status_code=status.HTTP_200_OK)
async def update_seeker_profile(
    schema: CustomerProfileUpdate,
    response: Response,
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.CUSTOMER)),
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Update profile details for the currently authenticated seeker (customer)."""
    try:
        timer = Timer()
        timer.start()
        updated_user = await user_service.update_customer_profile(
            user_id=current_user.id,
            first_name=schema.first_name,
            last_name=schema.last_name,
            phone_number=schema.phone_number
        )
        await system_logger.metric('update_seeker_profile', timer.stop(), source='profile.update_seeker_profile')
        return BaseAPIResponse[UserResponse](
            data=UserResponse.model_validate(updated_user),
            detail="Seeker profile updated successfully.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        await system_logger.warn('update_seeker_profile failed', source='profile.update_seeker_profile', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'update_seeker_profile error: {str(e)}', source='profile.update_seeker_profile')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during profile update."
        )


@router.put("/location", response_model=BaseAPIResponse[None], status_code=status.HTTP_200_OK)
async def update_location(
    schema: UpdateLocation,
    response: Response,
    current_user: UserResponse = Depends(GetCurrentUser()),
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Update the live last known location of the currently authenticated user (seeker or tasker)."""
    try:
        timer = Timer()
        timer.start()
        await user_service.update_user_location(
            user_id=current_user.id,
            user_type=current_user.type,
            latitude=schema.latitude,
            longitude=schema.longitude,
            address_line=schema.address_line,
            region_id=schema.region_id
        )
        await system_logger.metric('update_location', timer.stop(), source='profile.update_location')
        return BaseAPIResponse[None](
            detail="Location updated successfully.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        await system_logger.warn('update_location failed', source='profile.update_location', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'update_location error: {str(e)}', source='profile.update_location')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating location."
        )


@router.put("/cloud-messaging-token", response_model=BaseAPIResponse[None], status_code=status.HTTP_200_OK)
async def update_cloud_messaging_token(
    schema: UpdateCloudMessagingToken,
    response: Response,
    current_user: UserResponse = Depends(GetCurrentUser()),
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Update the cloud messaging token for push notifications."""
    try:
        timer = Timer()
        timer.start()
        await user_service.update_cloud_messaging_token(current_user.id, schema.token, schema.platform)
        await system_logger.metric('update_cloud_messaging_token', timer.stop(), source='profile.update_cloud_messaging_token')
        return BaseAPIResponse[None](
            detail="Cloud messaging token updated successfully.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        await system_logger.warn('update_cloud_messaging_token failed', source='profile.update_cloud_messaging_token', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'update_cloud_messaging_token error: {str(e)}', source='profile.update_cloud_messaging_token')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating the token."
        )


@router.post("/provider/services", response_model=BaseAPIResponse[None], status_code=status.HTTP_200_OK)
async def attach_provider_service(
    schema: AttachProviderService,
    response: Response,
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)),
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Add a service to the authenticated provider's account. Max 3 services allowed."""
    try:
        timer = Timer()
        timer.start()
        await user_service.attach_provider_service(
            user_id=current_user.id,
            service_id=schema.service_id
        )
        await system_logger.metric('attach_provider_service', timer.stop(), source='profile.attach_provider_service')
        return BaseAPIResponse[None](
            detail="Service successfully added.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        await system_logger.warn('attach_provider_service failed', source='profile.attach_provider_service', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'attach_provider_service error: {str(e)}', source='profile.attach_provider_service')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}"
        )


@router.delete("/provider/services/{service_id}", response_model=BaseAPIResponse[None], status_code=status.HTTP_200_OK)
async def remove_provider_service(
    service_id: str,
    response: Response,
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)),
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Remove a service from the authenticated provider's account."""
    try:
        timer = Timer()
        timer.start()
        await user_service.remove_provider_service(
            user_id=current_user.id,
            service_id=service_id
        )
        await system_logger.metric('remove_provider_service', timer.stop(), source='profile.remove_provider_service')
        return BaseAPIResponse[None](
            detail="Service successfully removed.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        await system_logger.warn('remove_provider_service failed', source='profile.remove_provider_service', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'remove_provider_service error: {str(e)}', source='profile.remove_provider_service')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}"
        )


@router.put("/region", response_model=BaseAPIResponse[UserResponse], status_code=status.HTTP_200_OK)
async def update_region(
    schema: UpdateRegion,
    response: Response,
    current_user: UserResponse = Depends(GetCurrentUser()),
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Update the region ID of the currently authenticated user."""
    try:
        timer = Timer()
        timer.start()
        updated_user = await user_service.update_user_region(
            user_id=current_user.id,
            region_id=schema.region_id
        )
        await system_logger.metric('update_region', timer.stop(), source='profile.update_region')
        return BaseAPIResponse[UserResponse](
            data=UserResponse.model_validate(updated_user),
            detail="Region updated successfully.",
            statusCode=status.HTTP_200_OK
        )
    except HTTPException as e:
        await system_logger.warn('update_region failed', source='profile.update_region', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'update_region error: {str(e)}', source='profile.update_region')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}"
        )


@router.put("/online-status", response_model=BaseAPIResponse[UserResponse], status_code=status.HTTP_200_OK)
async def update_online_status(
    schema: UpdateOnlineStatus,
    current_user: UserResponse = Depends(GetCurrentUser(required_type=UserType.PROVIDER)),
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Toggle online presence status for the authenticated service provider."""
    try:
        timer = Timer()
        timer.start()
        updated_user = await user_service.update_provider_online_status(
            user_id=current_user.id,
            is_online=schema.is_online,
        )
        await system_logger.metric('update_online_status', timer.stop(), source='profile.update_online_status')
        return BaseAPIResponse[UserResponse](
            data=UserResponse.model_validate(updated_user),
            detail=f"Provider online status set to {schema.is_online}.",
            statusCode=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('update_online_status failed', source='profile.update_online_status', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'update_online_status error: {str(e)}', source='profile.update_online_status')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating online status.",
        )


@router.post("/location/ping", response_model=BaseAPIResponse[None], status_code=status.HTTP_200_OK)
async def ping_location(
    schema: LocationPing,
    current_user: UserResponse = Depends(GetCurrentUser(required_type=UserType.PROVIDER)),
    user_service: UserService = Depends(get_user_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Real-time provider location heartbeat ping to Redis geospatial index."""
    try:
        timer = Timer()
        timer.start()
        await user_service.ping_provider_location(
            user_id=current_user.id,
            latitude=schema.latitude,
            longitude=schema.longitude,
        )
        await system_logger.metric('ping_location', timer.stop(), source='profile.ping_location')
        return BaseAPIResponse[None](
            detail="Provider location ping received.",
            statusCode=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('ping_location failed', source='profile.ping_location', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'ping_location error: {str(e)}', source='profile.ping_location')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while pinging location.",
        )

