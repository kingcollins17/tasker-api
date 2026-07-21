import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)

class PaymentInitializationResponse(BaseModel):
    checkout_url: Optional[str] = None
    reference: Optional[str] = None
    access_code: Optional[str] = None
    user_id: Optional[str] = None
    username: Optional[str] = None
    phone_number: Optional[str] = None

class PaymentSendResponse(BaseModel):
    is_successful: Optional[bool] = None
    user_id: Optional[str] = None
    username: Optional[str] = None
    phone_number: Optional[str] = None

class TransactionVerificationResponse(BaseModel):
    is_successful: Optional[bool] = None
    amount: Optional[float] = None
    reference: Optional[str] = None
    currency: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class PaymentAccountResponse(BaseModel):
    payment_account_id: Optional[str] = None
    account_number: Optional[str] = None
    account_name: Optional[str] = None
    bank_code: Optional[str] = None
    user_id: Optional[str] = None
    phone_number: Optional[str] = None

class PaymentGateway(ABC):
    """Abstract base class representing a Payment Gateway."""

    @abstractmethod
    async def receive_payment(
        self,
        email: str,
        amount: float,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        phone_number: Optional[str] = None,
        callback_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> PaymentInitializationResponse:
        """Initialize a payment transaction on the gateway."""
        pass

    @abstractmethod
    async def send_payment(
        self,
        amount: float,
        recipient_code: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        phone_number: Optional[str] = None,
        reason: Optional[str] = None,
        reference: Optional[str] = None
    ) -> PaymentSendResponse:
        """Initiate a transfer/payout to a recipient."""
        pass

    @abstractmethod
    async def verify_transaction(self, reference: str) -> TransactionVerificationResponse:
        """Verify the status of a transaction on the gateway."""
        pass

    @abstractmethod
    async def create_payment_account(
        self,
        bank_code: str,
        account_number: str,
        account_name: str,
        email: str,
        user_id: str,
        phone_number: Optional[str] = None
    ) -> PaymentAccountResponse:
        """Create a payment account on the gateway."""
        pass


class PaystackPaymentGateway(PaymentGateway):
    """Mock Paystack implementation of the PaymentGateway interface."""

    def __init__(self, secret_key: str, base_url: str = "https://api.paystack.co"):
        self.secret_key = secret_key
        self.base_url = base_url

    async def receive_payment(
        self,
        email: str,
        amount: float,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        phone_number: Optional[str] = None,
        callback_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> PaymentInitializationResponse:
        logger.info(f"Mock receive_payment: email={email}, amount={amount}, user_id={user_id}, username={username}, phone_number={phone_number}")
        return PaymentInitializationResponse(
            checkout_url="https://mock.checkout.url/123",
            reference="mock_ref_123",
            access_code="mock_access_code",
            user_id=user_id,
            username=username,
            phone_number=phone_number
        )

    async def send_payment(
        self,
        amount: float,
        recipient_code: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        phone_number: Optional[str] = None,
        reason: Optional[str] = None,
        reference: Optional[str] = None
    ) -> PaymentSendResponse:
        logger.info(f"Mock send_payment: amount={amount}, recipient={recipient_code}, user_id={user_id}, username={username}, phone_number={phone_number}")
        return PaymentSendResponse(
            is_successful=True,
            user_id=user_id,
            username=username,
            phone_number=phone_number
        )

    async def verify_transaction(self, reference: str) -> TransactionVerificationResponse:
        logger.info(f"Mock verify_transaction: reference={reference}")
        return TransactionVerificationResponse(
            is_successful=True,
            amount=1000.0,
            reference=reference,
            currency="NGN",
            metadata={"mock": "data"}
        )

    async def create_payment_account(
        self,
        bank_code: str,
        account_number: str,
        account_name: str,
        email: str,
        user_id: str,
        phone_number: Optional[str] = None
    ) -> PaymentAccountResponse:
        logger.info(f"Mock create_payment_account: bank={bank_code}, account={account_number}, email={email}")
        return PaymentAccountResponse(
            payment_account_id="mock_payment_account_id_123",
            account_number=account_number,
            account_name=account_name,
            bank_code=bank_code,
            user_id=user_id,
            phone_number=phone_number
        )


def get_paystack_gateway() -> PaystackPaymentGateway:
    """Dependency provider function for PaystackPaymentGateway."""
    return PaystackPaymentGateway(
        secret_key=settings.PAYSTACK_SECRET_KEY,
        base_url=settings.PAYSTACK_BASE_URL
    )
