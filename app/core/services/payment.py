from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Result & Error Types ──────────────────────────────────────────────────────


@dataclass
class TransferResult:
    """Normalized result from a payment provider transfer operation."""

    provider_transfer_id: Optional[str] = None
    status: str = ""
    raw_response: Dict[str, Any] = field(default_factory=dict)


class TemporaryProviderError(Exception):
    """Retryable provider error (timeouts, 5xx, 429, network issues)."""

    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.code = code


class PermanentProviderError(Exception):
    """Non-retryable provider error (invalid account, bad request, etc.)."""

    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.code = code


class PaymentInitializationResponse(BaseModel):
    checkout_url: Optional[str] = None
    reference: Optional[str] = None
    access_code: Optional[str] = None
    user_id: Optional[str] = None
    fullname: Optional[str] = None
    phone_number: Optional[str] = None
    metadata: Optional[dict] = None
    amount: Optional[float] = None


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
        fullname: Optional[str] = None,
        phone_number: Optional[str] = None,
        callback_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PaymentInitializationResponse:
        """Initialize a payment transaction on the gateway."""
        pass

    @abstractmethod
    async def send_payment(
        self,
        amount: float,
        recipient_code: str,
        user_id: Optional[str] = None,
        fullname: Optional[str] = None,
        phone_number: Optional[str] = None,
        reason: Optional[str] = None,
        reference: Optional[str] = None,
    ) -> PaymentSendResponse:
        """Initiate a transfer/payout to a recipient."""
        pass

    @abstractmethod
    async def verify_transaction(
        self, reference: str
    ) -> TransactionVerificationResponse:
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
        phone_number: Optional[str] = None,
    ) -> PaymentAccountResponse:
        """Create a payment account on the gateway."""
        pass

    @abstractmethod
    async def transfer(
        self,
        *,
        amount: float,
        currency: str,
        destination: str,
        idempotency_key: str,
        reference: Optional[str] = None,
        user_id: Optional[str] = None,
        task_id: Optional[str] = None,
        payment_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TransferResult:
        """Initiate a transfer to a destination (provider recipient code).

        Must raise TemporaryProviderError for retryable failures and
        PermanentProviderError for non-retryable failures.
        """
        pass

    @abstractmethod
    async def get_transfer(
        self,
        provider_transfer_id: str,
    ) -> TransferResult:
        """Look up a transfer by its provider-assigned ID for reconciliation."""
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
        fullname: Optional[str] = None,
        phone_number: Optional[str] = None,
        callback_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PaymentInitializationResponse:
        logger.info(
            f"Mock receive_payment: email={email}, amount={amount}, user_id={user_id}, fullname={fullname}, phone_number={phone_number}"
        )
        meta = metadata or {}
        if user_id and "user_id" not in meta:
            meta["user_id"] = user_id

        return PaymentInitializationResponse(
            checkout_url="https://mock.checkout.url/123",
            reference="mock_ref_123",
            access_code="mock_access_code",
            user_id=user_id,
            fullname=fullname,
            phone_number=phone_number,
            metadata=meta,
            amount=amount,
        )

    async def send_payment(
        self,
        amount: float,
        recipient_code: str,
        user_id: Optional[str] = None,
        fullname: Optional[str] = None,
        phone_number: Optional[str] = None,
        reason: Optional[str] = None,
        reference: Optional[str] = None,
    ) -> PaymentSendResponse:
        logger.info(
            f"Mock send_payment: amount={amount}, recipient={recipient_code}, user_id={user_id}, fullname={fullname}, phone_number={phone_number}"
        )
        return PaymentSendResponse(
            is_successful=True,
            user_id=user_id,
            username=fullname,
            phone_number=phone_number,
        )

    async def verify_transaction(
        self, reference: str
    ) -> TransactionVerificationResponse:
        logger.info(f"Mock verify_transaction: reference={reference}")
        return TransactionVerificationResponse(
            is_successful=True,
            amount=1000.0,
            reference=reference,
            currency="NGN",
            metadata={"mock": "data"},
        )

    async def create_payment_account(
        self,
        bank_code: str,
        account_number: str,
        account_name: str,
        email: str,
        user_id: str,
        phone_number: Optional[str] = None,
    ) -> PaymentAccountResponse:
        logger.info(
            f"Mock create_payment_account: bank={bank_code}, account={account_number}, email={email}"
        )
        return PaymentAccountResponse(
            payment_account_id="mock_payment_account_id_123",
            account_number=account_number,
            account_name=account_name,
            bank_code=bank_code,
            user_id=user_id,
            phone_number=phone_number,
        )

    async def transfer(
        self,
        *,
        amount: float,
        currency: str,
        destination: str,
        idempotency_key: str,
        reference: Optional[str] = None,
        user_id: Optional[str] = None,
        task_id: Optional[str] = None,
        payment_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TransferResult:
        """Initiate a Paystack transfer to a recipient code.

        Maps gateway responses and exceptions to TransferResult,
        TemporaryProviderError, or PermanentProviderError.
        """
        try:
            transfer_meta = metadata or {}
            if user_id:
                transfer_meta["user_id"] = user_id
            if task_id:
                transfer_meta["task_id"] = task_id
            if payment_id:
                transfer_meta["payment_id"] = payment_id
            transfer_meta["idempotency_key"] = idempotency_key

            response = await self.send_payment(
                amount=amount,
                recipient_code=destination,
                user_id=user_id,
                reference=reference or idempotency_key,
                reason=f"Tasker provider payout (key={idempotency_key})",
            )

            if response.is_successful:
                return TransferResult(
                    provider_transfer_id=reference or idempotency_key,
                    status="success",
                    raw_response=response.model_dump(),
                )

            # Provider returned a non-success response without raising
            raise PermanentProviderError(
                message="Provider returned unsuccessful response",
                code="PROVIDER_REJECTED",
            )

        except (TemporaryProviderError, PermanentProviderError):
            # Re-raise our own error types
            raise

        except TimeoutError as exc:
            raise TemporaryProviderError(
                message=f"Provider request timed out: {exc}",
                code="TIMEOUT",
            ) from exc

        except ConnectionError as exc:
            raise TemporaryProviderError(
                message=f"Provider connection failed: {exc}",
                code="CONNECTION_ERROR",
            ) from exc

        except Exception as exc:
            # Unknown errors are treated as temporary to allow reconciliation
            logger.exception("Unexpected error during provider transfer")
            raise TemporaryProviderError(
                message=f"Unexpected provider error: {exc}",
                code="UNKNOWN",
            ) from exc

    async def get_transfer(
        self,
        provider_transfer_id: str,
    ) -> TransferResult:
        """Look up a Paystack transfer by reference for reconciliation.

        Uses verify_transaction as a proxy — real implementation would
        call the Paystack Transfers API.
        """
        try:
            response = await self.verify_transaction(provider_transfer_id)
            status = "success" if response.is_successful else "failed"
            return TransferResult(
                provider_transfer_id=provider_transfer_id,
                status=status,
                raw_response=response.model_dump(),
            )
        except Exception as exc:
            logger.exception("Failed to look up transfer from provider")
            raise TemporaryProviderError(
                message=f"Provider lookup failed: {exc}",
                code="LOOKUP_FAILED",
            ) from exc


def get_paystack_gateway() -> PaystackPaymentGateway:
    """Dependency provider function for PaystackPaymentGateway."""
    return PaystackPaymentGateway(
        secret_key=settings.PAYSTACK_SECRET_KEY, base_url=settings.PAYSTACK_BASE_URL
    )

