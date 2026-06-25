from .email import EmailService
from .sms import SMSService
from .cache import CacheService, get_cache_service
from .payment import PaymentGateway, PaystackPaymentGateway, get_paystack_gateway
from .storage import StorageService, MockStorageService, get_storage_service
from .cloud_messaging import CloudMessagingService, MockCloudMessagingService, get_cloud_messaging_service
from .otp import (
    OTPService,
    OTPError,
    OTPRateLimitError,
    OTPVerificationError,
    OTPMaxAttemptsReachedError,
)
from fastapi import Depends

# Instantiate singleton instances for application-wide use
email_service = EmailService()
sms_service = SMSService()


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
    "CacheService",
    "PaymentGateway",
    "PaystackPaymentGateway",
    "StorageService",
    "MockStorageService",
    "CloudMessagingService",
    "MockCloudMessagingService",
    "OTPService",
    "OTPError",
    "OTPRateLimitError",
    "OTPVerificationError",
    "OTPMaxAttemptsReachedError",
    "email_service",
    "sms_service",
    "get_cache_service",
    "get_paystack_gateway",
    "get_otp_service",
    "get_storage_service",
    "get_cloud_messaging_service",
]



