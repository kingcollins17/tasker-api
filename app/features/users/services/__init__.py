"""User domain service package re-exporting modular sub-services and composite UserService facade."""

from app.features.users.services.auth_service import UserAuthService, get_user_auth_service
from app.features.users.services.user_service import UserService, get_user_service
from app.features.users.services.customer_profile_service import CustomerProfileService, get_customer_profile_service
from app.features.users.services.location_device_service import UserLocationDeviceService, get_user_location_device_service
from app.features.users.services.provider_profile_service import ProviderProfileService, get_provider_profile_service
from app.features.users.services.kyc_service import KYCService, get_kyc_service
from app.features.users.services.user_management_service import UserManagementService, get_user_management_service

__all__ = [
    "UserService",
    "get_user_service",
    "UserAuthService",
    "get_user_auth_service",
    "CustomerProfileService",
    "get_customer_profile_service",
    "ProviderProfileService",
    "get_provider_profile_service",
    "UserLocationDeviceService",
    "get_user_location_device_service",
    "KYCService",
    "get_kyc_service",
    "UserManagementService",
    "get_user_management_service",
]

