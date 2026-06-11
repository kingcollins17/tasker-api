from .email import EmailService
from .sms import SMSService
from .cache import CacheService, get_cache_service
from .payment import PaymentGateway, PaystackPaymentGateway, get_paystack_gateway

# Instantiate singleton instances for application-wide use
email_service = EmailService()
sms_service = SMSService()

__all__ = [
    "EmailService",
    "SMSService",
    "CacheService",
    "PaymentGateway",
    "PaystackPaymentGateway",
    "email_service",
    "sms_service",
    "get_cache_service",
    "get_paystack_gateway",
]


