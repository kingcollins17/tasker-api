from app.core.utils.timer import Timer
from app.core.services.logger_service import LoggerService, get_logger_service
import inspect
from fastapi import HTTPException
from fastapi import APIRouter, Depends, status, Response
from app.core.error_handler import AppErrorHandler
from sqlmodel import select, delete, col
from app.core.api_response import BaseAPIResponse
from app.core.deps import GetCurrentUser
from app.core.models.users import UserType, User
from app.core.schemas.users import MinimalProviderResponse
from app.core.repository import Repository, GetRepository
from app.core.models.services import Service, ProviderServiceLink
from app.features.users.schemas import (
    UserResponse,
    ProviderProfileUpdate,
    CustomerProfileUpdate,
    UpdateLocation,
    UpdateCloudMessagingToken,
    AttachProviderService,
    BulkProviderServices,
    ServiceResponse,
    UpdateRegion,
    UpdateOnlineStatus,
    LocationPing,
    ProviderAvailabilityResponse,
    UpdateProviderAvailabilityBlock,
)
from typing import List
from app.features.users.services import UserService, get_user_service
from app.core.services.availability_service import AvailabilityService, get_availability_service

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
            status_code=status.HTTP_200_OK
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
            status_code=status.HTTP_200_OK
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
            status_code=status.HTTP_200_OK
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
            status_code=status.HTTP_200_OK
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
            status_code=status.HTTP_200_OK
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
            status_code=status.HTTP_200_OK
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


@router.post("/provider/services/bulk", response_model=BaseAPIResponse[None], status_code=status.HTTP_200_OK)
async def bulk_attach_provider_services(
    schema: BulkProviderServices,
    response: Response,
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)),
    service_repo: Repository[Service] = Depends(GetRepository(Service)),
    link_repo: Repository[ProviderServiceLink] = Depends(GetRepository(ProviderServiceLink)),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Bulk add services to the authenticated provider's account. Max 3 services allowed in total."""
    try:
        timer = Timer()
        timer.start()

        if not schema.service_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No service IDs provided."
            )

        requested_ids = list(set(schema.service_ids))

        # Check if all requested services exist and are active
        service_stmt = select(Service).where(col(Service.id).in_(requested_ids))
        service_result = await service_repo.execute(service_stmt)
        found_services = list(service_result.all())
        found_ids = {s.id for s in found_services}

        missing_ids = set(requested_ids) - found_ids
        if missing_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Service(s) not found: {', '.join(sorted(missing_ids))}"
            )

        inactive_services = [s for s in found_services if not s.is_active]
        if inactive_services:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot add inactive service(s): {', '.join(sorted([s.id for s in inactive_services]))}"
            )

        # Fetch existing links for provider
        link_stmt = select(ProviderServiceLink).where(
            ProviderServiceLink.provider_id == current_user.id
        )
        link_result = await link_repo.execute(link_stmt)
        existing_links = list(link_result.all())
        existing_service_ids = {link.service_id for link in existing_links}

        new_service_ids = [sid for sid in requested_ids if sid not in existing_service_ids]

        if len(existing_service_ids) + len(new_service_ids) > 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A provider can have a maximum of 3 services."
            )

        if new_service_ids:
            new_links = [
                ProviderServiceLink(provider_id=current_user.id, service_id=sid)
                for sid in new_service_ids
            ]
            await link_repo.bulk_add(new_links)

        await system_logger.metric('bulk_attach_provider_services', timer.stop(), source='profile.bulk_attach_provider_services')
        return BaseAPIResponse[None](
            detail="Services successfully added.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException as e:
        await system_logger.warn('bulk_attach_provider_services failed', source='profile.bulk_attach_provider_services', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'bulk_attach_provider_services error: {str(e)}', source='profile.bulk_attach_provider_services')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}"
        )


@router.delete("/provider/services/bulk", response_model=BaseAPIResponse[None], status_code=status.HTTP_200_OK)
async def bulk_remove_provider_services(
    schema: BulkProviderServices,
    response: Response,
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)),
    link_repo: Repository[ProviderServiceLink] = Depends(GetRepository(ProviderServiceLink)),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Bulk remove services from the authenticated provider's account."""
    try:
        timer = Timer()
        timer.start()

        if not schema.service_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No service IDs provided."
            )

        delete_stmt = delete(ProviderServiceLink).where(
            col(ProviderServiceLink.provider_id) == current_user.id,
            col(ProviderServiceLink.service_id).in_(schema.service_ids)
        )
        await link_repo.execute(delete_stmt)
        await link_repo.commit()

        await system_logger.metric('bulk_remove_provider_services', timer.stop(), source='profile.bulk_remove_provider_services')
        return BaseAPIResponse[None](
            detail="Services successfully removed.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException as e:
        await system_logger.warn('bulk_remove_provider_services failed', source='profile.bulk_remove_provider_services', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'bulk_remove_provider_services error: {str(e)}', source='profile.bulk_remove_provider_services')
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
            status_code=status.HTTP_200_OK
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
            status_code=status.HTTP_200_OK
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
            status_code=status.HTTP_200_OK,
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
            status_code=status.HTTP_200_OK,
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


@router.get("/provider/availability", response_model=BaseAPIResponse[List[ProviderAvailabilityResponse]], status_code=status.HTTP_200_OK)
async def get_provider_availability(
    current_user: UserResponse = Depends(GetCurrentUser(required_type=UserType.PROVIDER)),
    availability_service: AvailabilityService = Depends(get_availability_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Get the weekly availability schedule for the authenticated provider."""
    try:
        timer = Timer()
        timer.start()
        blocks = await availability_service.get_provider_availability(current_user.id)
        await system_logger.metric('get_availability', timer.stop(), source='profile.get_availability')
        return BaseAPIResponse[List[ProviderAvailabilityResponse]](
            data=[ProviderAvailabilityResponse.model_validate(b) for b in blocks],
            detail="Availability fetched successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('get_availability failed', source='profile.get_availability', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'get_availability error: {str(e)}', source='profile.get_availability')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred.",
        )

@router.put("/provider/availability/{availability_id}", response_model=BaseAPIResponse[ProviderAvailabilityResponse], status_code=status.HTTP_200_OK)
async def update_provider_availability(
    availability_id: str,
    schema: UpdateProviderAvailabilityBlock,
    current_user: UserResponse = Depends(GetCurrentUser(required_type=UserType.PROVIDER)),
    availability_service: AvailabilityService = Depends(get_availability_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Update an availability block for the authenticated provider (day_of_week, start_time, end_time, is_active)."""
    try:
        timer = Timer()
        timer.start()
        block = await availability_service.update_availability_block(
            availability_id=availability_id,
            provider_id=current_user.id,
            day_of_week=schema.day_of_week,
            start_time=schema.start_time,
            end_time=schema.end_time,
            is_active=schema.is_active,
        )
        await system_logger.metric('update_availability', timer.stop(), source='profile.update_availability')
        return BaseAPIResponse[ProviderAvailabilityResponse](
            data=ProviderAvailabilityResponse.model_validate(block),
            detail="Availability block updated successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('update_availability failed', source='profile.update_availability', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'update_availability error: {str(e)}', source='profile.update_availability')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating availability.",
        )


@router.post("/provider/availability/default", response_model=BaseAPIResponse[List[ProviderAvailabilityResponse]], status_code=status.HTTP_201_CREATED)
async def create_default_provider_availability(
    current_user: UserResponse = Depends(GetCurrentUser(required_type=UserType.PROVIDER)),
    availability_service: AvailabilityService = Depends(get_availability_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Create default availability blocks for the authenticated provider if none exist."""
    try:
        timer = Timer()
        timer.start()
        blocks = await availability_service.create_default_availability(current_user.id)
        await system_logger.metric('create_default_availability', timer.stop(), source='profile.create_default_availability')
        return BaseAPIResponse[List[ProviderAvailabilityResponse]](
            data=[ProviderAvailabilityResponse.model_validate(b) for b in blocks],
            detail="Default availability blocks created successfully.",
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException as e:
        await system_logger.warn('create_default_availability failed', source='profile.create_default_availability', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'create_default_availability error: {str(e)}', source='profile.create_default_availability')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating default availability.",
        )


