"""Composite UserService facade delegating operations to domain sub-services."""

from typing import Optional
from fastapi import Depends

from app.core.models.regions import Region
from app.core.models.users import CustomerProfile, ProviderProfile, User, UserDevice, UserLocation, UserType
from app.core.repository import GetRepository, Repository
from app.core.services import OTPService, get_otp_service
from app.core.services.provider_location import ProviderLocationService, get_provider_location_service
from app.core.services.availability_service import AvailabilityService, get_availability_service, get_availability_service_manual
from app.features.users.schemas import UserLogin, UserRegister
from app.features.users.services.auth_service import UserAuthService, get_user_auth_service
from app.features.users.services.customer_profile_service import CustomerProfileService, get_customer_profile_service
from app.features.users.services.location_device_service import UserLocationDeviceService, get_user_location_device_service
from app.features.users.services.provider_profile_service import ProviderProfileService, get_provider_profile_service


class UserService:
    """Composite facade service organizing authentication, customer profiles,

    provider profiles, and location/device operations into dedicated sub-services.
    """

    def __init__(
        self,
        user_repo: Repository[User],
        customer_repo: Repository[CustomerProfile],
        provider_repo: Repository[ProviderProfile],
        otp_service: OTPService,
        region_repo: Repository[Region],
        location_repo: Repository[UserLocation],
        device_repo: Repository[UserDevice],
        availability_service: AvailabilityService,
        provider_location_service: ProviderLocationService,
        auth_service: UserAuthService,
        customer_service: CustomerProfileService,
        provider_service: ProviderProfileService,
        location_service: UserLocationDeviceService,
    ):
        # Repositories & underlying services exposed for backward compatibility
        self.user_repo = user_repo
        self.customer_repo = customer_repo
        self.provider_repo = provider_repo
        self.otp_service = otp_service
        self.region_repo = region_repo
        self.location_repo = location_repo
        self.device_repo = device_repo
        self.provider_location_service = provider_location_service
        self.availability_service = availability_service

        # Sub-services
        self.auth = auth_service
        self.customer = customer_service
        self.provider = provider_service
        self.location_device = location_service

    # ── Authentication & Identity Workflows ───────────────────────────────

    async def register_user(self, schema: UserRegister) -> User:
        """Register a new user and initialize their role profile."""
        return await self.auth.register_user(schema)

    async def login_user(self, schema: UserLogin) -> dict:
        """Authenticate user credentials and issue JWT access token."""
        return await self.auth.login_user(schema)

    async def get_user(self, user_id: str) -> Optional[User]:
        """Fetch user by unique primary key."""
        return await self.auth.get_user(user_id)

    async def request_email_otp(self, email: str) -> None:
        """Send verification code to user's email address."""
        await self.auth.request_email_otp(email)

    async def verify_email_otp(self, email: str, code: str) -> User:
        """Verify email OTP code."""
        return await self.auth.verify_email_otp(email, code)

    async def request_phone_otp(self, phone_number: str) -> None:
        """Send verification code to user's phone number."""
        await self.auth.request_phone_otp(phone_number)

    async def verify_phone_otp(self, phone_number: str, code: str) -> User:
        """Verify phone SMS OTP code."""
        return await self.auth.verify_phone_otp(phone_number, code)

    async def verify_otp(self, target: str, channel: str, code: str) -> bool:
        """Verify OTP code without mutating user record."""
        return await self.auth.verify_otp(target, channel, code)

    # ── Customer Profile Workflows ──────────────────────────────────────────

    async def update_customer_profile(
        self,
        user_id: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        phone_number: Optional[str] = None,
    ) -> User:
        """Update seeker (customer) profile attributes and phone number."""
        return await self.customer.update_customer_profile(
            user_id=user_id,
            first_name=first_name,
            last_name=last_name,
            phone_number=phone_number,
        )

    # ── Provider Profile & KYC Workflows ───────────────────────────────────

    async def update_provider_profile(
        self,
        user_id: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        gender: Optional[str] = None,
        phone_number: Optional[str] = None,
    ) -> User:
        """Update provider details and phone number."""
        return await self.provider.update_provider_profile(
            user_id=user_id,
            first_name=first_name,
            last_name=last_name,
            gender=gender,
            phone_number=phone_number,
        )

    async def submit_kyc(
        self,
        user_id: str,
        id_type: str,
        id_number: str,
        id_doc_url: str,
        selfie_url: str,
    ) -> ProviderProfile:
        """Submit KYC details and transition status to SUBMITTED."""
        return await self.provider.submit_kyc(
            user_id=user_id,
            id_type=id_type,
            id_number=id_number,
            id_doc_url=id_doc_url,
            selfie_url=selfie_url,
        )

    async def submit_kyc_selfie(self, user_id: str, selfie_url: str) -> ProviderProfile:
        """Submit KYC liveness verification selfie."""
        return await self.provider.submit_kyc_selfie(user_id=user_id, selfie_url=selfie_url)

    async def submit_kyc_document(
        self, user_id: str, id_type: str, id_number: str, id_doc_url: str
    ) -> ProviderProfile:
        """Submit KYC document details."""
        return await self.provider.submit_kyc_document(
            user_id=user_id,
            id_type=id_type,
            id_number=id_number,
            id_doc_url=id_doc_url,
        )

    async def attach_provider_service(self, user_id: str, service_id: str) -> None:
        """Link a service to provider account (max 3 services)."""
        await self.provider.attach_provider_service(user_id=user_id, service_id=service_id)

    async def remove_provider_service(self, user_id: str, service_id: str) -> None:
        """Remove a service link from provider account."""
        await self.provider.remove_provider_service(user_id=user_id, service_id=service_id)

    async def update_provider_online_status(self, user_id: str, is_online: bool) -> User:
        """Toggle online status and update presence in Redis spatial index."""
        return await self.provider.update_provider_online_status(
            user_id=user_id, is_online=is_online
        )

    # ── Location & Device Token Workflows ─────────────────────────────────

    async def update_user_location(
        self,
        user_id: str,
        user_type: UserType,
        latitude: float,
        longitude: float,
        address_line: Optional[str] = None,
        region_id: Optional[str] = None,
    ) -> None:
        """Upsert static spatial coordinates in user_locations table."""
        await self.location_device.update_user_location(
            user_id=user_id,
            user_type=user_type,
            latitude=latitude,
            longitude=longitude,
            address_line=address_line,
            region_id=region_id,
        )

    async def update_cloud_messaging_token(
        self, user_id: str, token: str, platform: str
    ) -> None:
        """Upsert cloud device registration messaging token."""
        await self.location_device.update_cloud_messaging_token(
            user_id=user_id, token=token, platform=platform
        )

    async def update_user_region(self, user_id: str, region_id: Optional[str]) -> User:
        """Update region assignment for user."""
        return await self.location_device.update_user_region(
            user_id=user_id, region_id=region_id
        )

    async def ping_provider_location(
        self, user_id: str, latitude: float, longitude: float
    ) -> None:
        """Publish high-frequency provider location heartbeat to Redis and DB."""
        await self.location_device.ping_provider_location(
            user_id=user_id, latitude=latitude, longitude=longitude
        )


def get_user_service(
    user_repo: Repository[User] = Depends(GetRepository(User)),
    customer_repo: Repository[CustomerProfile] = Depends(
        GetRepository(CustomerProfile)
    ),
    provider_repo: Repository[ProviderProfile] = Depends(
        GetRepository(ProviderProfile)
    ),
    otp_service: OTPService = Depends(get_otp_service),
    region_repo: Repository[Region] = Depends(GetRepository(Region)),
    location_repo: Repository[UserLocation] = Depends(GetRepository(UserLocation)),
    device_repo: Repository[UserDevice] = Depends(GetRepository(UserDevice)),
    availability_service: AvailabilityService = Depends(get_availability_service),
    provider_location_service: ProviderLocationService = Depends(
        get_provider_location_service
    ),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    customer_service: CustomerProfileService = Depends(get_customer_profile_service),
    provider_service: ProviderProfileService = Depends(get_provider_profile_service),
    location_service: UserLocationDeviceService = Depends(get_user_location_device_service),
) -> UserService:
    """Dependency provider injecting repositories and sub-services into composite UserService."""
    return UserService(
        user_repo=user_repo,
        customer_repo=customer_repo,
        provider_repo=provider_repo,
        otp_service=otp_service,
        region_repo=region_repo,
        location_repo=location_repo,
        device_repo=device_repo,
        availability_service=availability_service,
        provider_location_service=provider_location_service,
        auth_service=auth_service,
        customer_service=customer_service,
        provider_service=provider_service,
        location_service=location_service,
    )
