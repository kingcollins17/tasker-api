from fastapi import Depends

from .cache import CacheService, get_cache_service
from .cloud_messaging import (
    CloudMessagingService,
    MockCloudMessagingService,
    get_cloud_messaging_service,
)
from .connection_manager import ConnectionManager, get_connection_manager
from .email import EmailService
from .notification_pubsub import (
    start_notification_listener,
    stop_notification_listener,
)
from .otp import (
    OTPError,
    OTPMaxAttemptsReachedError,
    OTPRateLimitError,
    OTPService,
    OTPVerificationError,
)
from .payment import (
    PaymentGateway,
    PaystackPaymentGateway,
    PermanentProviderError,
    TemporaryProviderError,
    TransferResult,
    get_paystack_gateway,
)
from .provider_location import (
    LocationPoint,
    NearbyProviderResult,
    PostGISProviderLocationService,
    ProviderLocationPing,
    ProviderLocationService,
    get_provider_location_service,
)
from .sms import SMSService
from .storage import MockStorageService, StorageService, get_storage_service
from .whatsapp import WhatsAppService
from app.features.services.pricing_engine import (
    PricingBreakdown,
    PricingCalculationRequest,
    PricingEngine,
)
from .matching_engine import MatchingEngine


# Instantiate singleton instances for application-wide use
email_service = EmailService()
sms_service = SMSService()
whatsapp_service = WhatsAppService()


def get_otp_service(
    cache_service: CacheService = Depends(get_cache_service)
) -> OTPService:
    """Dependency provider function for OTPService."""
    return OTPService(
        cache_service=cache_service,
        email_service=email_service,
        sms_service=sms_service,
    )


__all__ = [
    "EmailService",
    "SMSService",
    "WhatsAppService",
    "CacheService",
    "PaymentGateway",
    "PaystackPaymentGateway",
    "PermanentProviderError",
    "TemporaryProviderError",
    "TransferResult",
    "StorageService",
    "MockStorageService",
    "CloudMessagingService",
    "MockCloudMessagingService",
    "ConnectionManager",
    "OTPService",
    "OTPError",
    "OTPRateLimitError",
    "OTPVerificationError",
    "OTPMaxAttemptsReachedError",
    "PricingEngine",
    "PricingCalculationRequest",
    "PricingBreakdown",
    "MatchingEngine",
    "ProviderLocationService",
    "PostGISProviderLocationService",
    "LocationPoint",
    "ProviderLocationPing",
    "NearbyProviderResult",
    "email_service",
    "sms_service",
    "whatsapp_service",
    "get_cache_service",
    "get_paystack_gateway",
    "get_otp_service",
    "get_provider_location_service",
    "get_storage_service",
    "get_cloud_messaging_service",
    "get_connection_manager",
    "start_notification_listener",
    "stop_notification_listener",
]
