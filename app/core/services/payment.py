from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import httpx
from app.core.config import settings

class PaymentGateway(ABC):
    """Abstract base class representing a Payment Gateway."""

    @abstractmethod
    async def initialize_transaction(
        self,
        email: str,
        amount: float,
        callback_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Initialize a payment transaction on the gateway."""
        pass

    @abstractmethod
    async def verify_transaction(self, reference: str) -> Dict[str, Any]:
        """Verify the status of a transaction on the gateway."""
        pass

    @abstractmethod
    async def create_transfer_recipient(
        self,
        type: str,
        name: str,
        account_number: str,
        bank_code: str,
        currency: str = "NGN"
    ) -> Dict[str, Any]:
        """Create a transfer/payout recipient on the gateway."""
        pass

    @abstractmethod
    async def initiate_transfer(
        self,
        amount: float,
        recipient_code: str,
        reference: Optional[str] = None,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """Initiate a transfer/payout to a recipient."""
        pass

    @abstractmethod
    async def create_subaccount(
        self,
        business_name: str,
        settlement_bank: str,
        account_number: str,
        percentage_charge: float,
        description: Optional[str] = None,
        primary_contact_email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a subaccount on the gateway."""
        pass

    @abstractmethod
    async def update_subaccount(
        self,
        subaccount_code: str,
        business_name: Optional[str] = None,
        settlement_bank: Optional[str] = None,
        account_number: Optional[str] = None,
        percentage_charge: Optional[float] = None,
        active: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Update a subaccount's details on the gateway."""
        pass

    @abstractmethod
    async def get_subaccount(self, subaccount_code: str) -> Dict[str, Any]:
        """Fetch details of a subaccount on the gateway."""
        pass


class PaystackPaymentGateway(PaymentGateway):
    """Paystack implementation of the PaymentGateway interface."""

    def __init__(self, secret_key: str, base_url: str = "https://api.paystack.co"):
        """Initializes the Paystack payment gateway.

        Args:
            secret_key: Paystack API secret key.
            base_url: Base URL for Paystack API.
        """
        self.secret_key = secret_key
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    async def initialize_transaction(
        self,
        email: str,
        amount: float,
        callback_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/transaction/initialize"
        amount_in_kobo = int(amount * 100)
        
        payload: Dict[str, Any] = {
            "email": email,
            "amount": amount_in_kobo,
        }
        if callback_url:
            payload["callback_url"] = callback_url
        if metadata:
            payload["metadata"] = metadata

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def verify_transaction(self, reference: str) -> Dict[str, Any]:
        url = f"{self.base_url}/transaction/verify/{reference}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def create_transfer_recipient(
        self,
        type: str,
        name: str,
        account_number: str,
        bank_code: str,
        currency: str = "NGN"
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/transferrecipient"
        payload = {
            "type": type,
            "name": name,
            "account_number": account_number,
            "bank_code": bank_code,
            "currency": currency,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def initiate_transfer(
        self,
        amount: float,
        recipient_code: str,
        reference: Optional[str] = None,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/transfer"
        amount_in_kobo = int(amount * 100)
        
        payload: Dict[str, Any] = {
            "source": "balance",
            "amount": amount_in_kobo,
            "recipient": recipient_code,
        }
        if reference:
            payload["reference"] = reference
        if reason:
            payload["reason"] = reason

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def create_subaccount(
        self,
        business_name: str,
        settlement_bank: str,
        account_number: str,
        percentage_charge: float,
        description: Optional[str] = None,
        primary_contact_email: Optional[str] = None
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/subaccount"
        payload: Dict[str, Any] = {
            "business_name": business_name,
            "settlement_bank": settlement_bank,
            "account_number": account_number,
            "percentage_charge": percentage_charge,
        }
        if description:
            payload["description"] = description
        if primary_contact_email:
            payload["primary_contact_email"] = primary_contact_email

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def update_subaccount(
        self,
        subaccount_code: str,
        business_name: Optional[str] = None,
        settlement_bank: Optional[str] = None,
        account_number: Optional[str] = None,
        percentage_charge: Optional[float] = None,
        active: Optional[bool] = None
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/subaccount/{subaccount_code}"
        payload: Dict[str, Any] = {}
        if business_name is not None:
            payload["business_name"] = business_name
        if settlement_bank is not None:
            payload["settlement_bank"] = settlement_bank
        if account_number is not None:
            payload["account_number"] = account_number
        if percentage_charge is not None:
            payload["percentage_charge"] = percentage_charge
        if active is not None:
            payload["active"] = active

        async with httpx.AsyncClient() as client:
            response = await client.put(url, json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def get_subaccount(self, subaccount_code: str) -> Dict[str, Any]:
        url = f"{self.base_url}/subaccount/{subaccount_code}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()


def get_paystack_gateway() -> PaystackPaymentGateway:
    """Dependency provider function for PaystackPaymentGateway."""
    return PaystackPaymentGateway(
        secret_key=settings.PAYSTACK_SECRET_KEY,
        base_url=settings.PAYSTACK_BASE_URL
    )
