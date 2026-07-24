"""User domain service package re-exporting modular sub-services and composite UserService facade."""

from app.features.users.services.auth_service import UserAuthService
from app.features.users.services.composite_service import UserService, get_user_service
from app.features.users.services.customer_profile_service import CustomerProfileService
from app.features.users.services.location_device_service import UserLocationDeviceService
from app.features.users.services.provider_profile_service import ProviderProfileService

__all__ = [
    "UserService",
    "get_user_service",
    "UserAuthService",
    "CustomerProfileService",
    "ProviderProfileService",
    "UserLocationDeviceService",
]
