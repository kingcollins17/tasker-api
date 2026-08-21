import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol

from app.core.config import settings
from app.core.services.payment import PaystackPaymentGateway, get_paystack_gateway

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


# ── Provider Protocol ─────────────────────────────────────────────────────────


class TransferProvider(Protocol):
    """Abstract interface for payment provider transfer operations.

    Keeps TransferService provider-agnostic so implementations can be
    swapped (Paystack, Flutterwave, mock) without changing business logic.
    """

    async def create_transfer(
        self,
        *,
        amount: float,
        currency: str,
        destination: str,
        idempotency_key: str,
        reference: Optional[str] = None,
    ) -> TransferResult:
        """Initiate a transfer to a destination (provider recipient code).

        Must raise TemporaryProviderError for retryable failures and
        PermanentProviderError for non-retryable failures.
        """
        ...

    async def get_transfer(
        self,
        provider_transfer_id: str,
    ) -> TransferResult:
        """Look up a transfer by its provider-assigned ID for reconciliation."""
        ...


# ── Paystack Implementation ──────────────────────────────────────────────────


class PaystackTransferProvider:
    """TransferProvider backed by the existing PaystackPaymentGateway.

    Wraps `send_payment` and classifies errors into temporary/permanent
    categories so TransferService can make correct retry decisions.
    """

    def __init__(self, gateway: PaystackPaymentGateway):
        self.gateway = gateway

    async def transfer(
        self,
        *,
        amount: float,
        currency: str,
        destination: str,
        idempotency_key: str,
        reference: Optional[str] = None,
    ) -> TransferResult:
        """Initiate a Paystack transfer to a recipient code.

        Maps gateway responses and exceptions to TransferResult,
        TemporaryProviderError, or PermanentProviderError.
        """
        try:
            response = await self.gateway.send_payment(
                amount=amount,
                recipient_code=destination,
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
            response = await self.gateway.verify_transaction(provider_transfer_id)
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


# ── Factory ───────────────────────────────────────────────────────────────────


def get_transfer_provider() -> PaystackTransferProvider:
    """Factory for obtaining a PaystackTransferProvider instance."""
    gateway = get_paystack_gateway()
    return PaystackTransferProvider(gateway=gateway)
