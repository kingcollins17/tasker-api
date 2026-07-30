"""User location, device messaging token, and spatial tracking sub-service."""

from typing import Optional
from fastapi import Depends, HTTPException, status

from app.core.logging import log_error
from app.core.models.regions import Region
from app.core.models.users import ProviderProfile, User, UserDevice, UserLocation, UserType
from app.core.repository import GetRepository, QueryOptions, Repository
from app.core.services.provider_location import ProviderLocationPing, ProviderLocationService, get_provider_location_service
from app.core.utils.datetime_helper import lagos_now


class UserLocationDeviceService:
    """Sub-service managing static spatial locations, FCM device messaging tokens,

    user region assignments, and real-time Redis location heartbeats.
    """

    def __init__(
        self,
        user_repo: Repository[User],
        provider_repo: Repository[ProviderProfile],
        region_repo: Repository[Region],
        location_repo: Repository[UserLocation],
        device_repo: Repository[UserDevice],
        provider_location_service: Optional[ProviderLocationService] = None,
    ):
        self.user_repo = user_repo
        self.provider_repo = provider_repo
        self.region_repo = region_repo
        self.location_repo = location_repo
        self.device_repo = device_repo
        self.provider_location_service = provider_location_service

    @log_error()
    async def update_user_location(
        self,
        user_id: str,
        user_type: UserType,
        latitude: float,
        longitude: float,
        address_line: Optional[str] = None,
        region_id: Optional[str] = None,
    ) -> None:
        """Update last known static location by upserting to the user_locations table."""
        wkt_point = f"POINT({longitude} {latitude})"

        locations = await self.location_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )

        if region_id:
            await self.user_repo.update(
                user_id, {"region_id": region_id, "updated_at": lagos_now()}
            )

        if locations:
            updates = {
                "last_known_location": wkt_point,
                "latitude": latitude,
                "longitude": longitude,
                "address_line": address_line,
                "updated_at": lagos_now(),
            }
            if region_id:
                updates["region_id"] = region_id
            await self.location_repo.update(locations[0].id, updates)
        else:
            new_location = UserLocation(
                user_id=user_id,
                last_known_location=wkt_point,
                latitude=latitude,
                longitude=longitude,
                address_line=address_line,
                region_id=region_id,
            )
            await self.location_repo.add(new_location)

    @log_error()
    async def update_cloud_messaging_token(
        self, user_id: str, token: str, platform: str
    ) -> None:
        """Update/upsert cloud messaging device token in user_devices table."""
        # 1. Check if token already exists in DB
        token_devices = await self.device_repo.get_all(
            QueryOptions(filters={"messaging_token": token})
        )

        # 2. Check if user already has a device for this platform
        user_platform_devices = await self.device_repo.get_all(
            QueryOptions(filters={"user_id": user_id, "platform": platform})
        )

        if token_devices:
            token_device = token_devices[0]
            if user_platform_devices and user_platform_devices[0].id != token_device.id:
                await self.device_repo.delete(user_platform_devices[0].id)

            await self.device_repo.update(
                token_device.id,
                {
                    "user_id": user_id,
                    "platform": platform,
                    "is_active": True,
                    "last_login_at": lagos_now(),
                    "updated_at": lagos_now(),
                },
            )
        else:
            if user_platform_devices:
                await self.device_repo.update(
                    user_platform_devices[0].id,
                    {
                        "messaging_token": token,
                        "is_active": True,
                        "last_login_at": lagos_now(),
                        "updated_at": lagos_now(),
                    },
                )
            else:
                new_device = UserDevice(
                    user_id=user_id,
                    platform=platform,
                    messaging_token=token,
                    is_active=True,
                )
                await self.device_repo.add(new_device)

    @log_error()
    async def update_user_region(self, user_id: str, region_id: Optional[str]) -> User:
        """Update the region for a user after validating it exists and is active."""
        if region_id is not None:
            region = await self.region_repo.get(region_id)
            if not region:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="The specified region does not exist.",
                )
            if not region.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="We are not active in this region yet",
                )

        await self.user_repo.update(user_id, {"region_id": region_id})
        user = await self.user_repo.get(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
            )
        await self.user_repo.refresh(user)
        return user

    @log_error()
    async def ping_provider_location(
        self, user_id: str, latitude: float, longitude: float
    ) -> None:
        """Heartbeat ping updating provider real-time coordinates in Redis and static location in DB."""
        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found.",
            )
        profile = profiles[0]

        # 1. Update static PostgreSQL location
        await self.update_user_location(
            user_id=user_id,
            user_type=UserType.PROVIDER,
            latitude=latitude,
            longitude=longitude,
        )



        # 3. Update heartbeat timestamp on profile
        await self.provider_repo.update(
            profile.id,
            {"last_heartbeat_at": lagos_now(), "updated_at": lagos_now()},
        )


def get_user_location_device_service(
    user_repo: Repository[User] = Depends(GetRepository(User)),
    provider_repo: Repository[ProviderProfile] = Depends(
        GetRepository(ProviderProfile)
    ),
    region_repo: Repository[Region] = Depends(GetRepository(Region)),
    location_repo: Repository[UserLocation] = Depends(GetRepository(UserLocation)),
    device_repo: Repository[UserDevice] = Depends(GetRepository(UserDevice)),
    provider_location_service: ProviderLocationService = Depends(
        get_provider_location_service
    ),
) -> UserLocationDeviceService:
    """Dependency provider injecting repositories and sub-services into UserLocationDeviceService."""
    return UserLocationDeviceService(
        user_repo=user_repo,
        provider_repo=provider_repo,
        region_repo=region_repo,
        location_repo=location_repo,
        device_repo=device_repo,
        provider_location_service=provider_location_service,
    )
