import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.core.services import get_paystack_gateway, PaystackPaymentGateway, PaymentGateway

def test_gateway_dependency():
    """Verify that get_paystack_gateway dependency returns a PaystackPaymentGateway."""
    gateway = get_paystack_gateway()
    assert isinstance(gateway, PaystackPaymentGateway)
    assert isinstance(gateway, PaymentGateway)
    assert gateway.secret_key == "sk_test_mock_key_from_env"
    assert gateway.base_url == "https://api.paystack.co"

@pytest.mark.anyio
async def test_initialize_transaction():
    gateway = PaystackPaymentGateway(secret_key="sk_test")
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": True, "data": {"authorization_url": "https://checkout.paystack.com/123"}}
    mock_response.raise_for_status = MagicMock()
    
    mock_client = MagicMock()
    async def aenter(*args, **kwargs):
        return mock_client
    async def aexit(*args, **kwargs):
        pass
    
    mock_client_context = MagicMock()
    mock_client_context.__aenter__ = aenter
    mock_client_context.__aexit__ = aexit
    
    mock_client.post = AsyncMock(return_value=mock_response)
    
    with patch("httpx.AsyncClient", return_value=mock_client_context):
        res = await gateway.initialize_transaction(
            email="test@user.com",
            amount=150.50,
            callback_url="http://callback",
            metadata={"custom": "field"}
        )
        assert res["status"] is True
        mock_client.post.assert_called_once_with(
            "https://api.paystack.co/transaction/initialize",
            json={
                "email": "test@user.com",
                "amount": 15050,  # 150.50 * 100
                "callback_url": "http://callback",
                "metadata": {"custom": "field"}
            },
            headers=gateway.headers
        )

@pytest.mark.anyio
async def test_verify_transaction():
    gateway = PaystackPaymentGateway(secret_key="sk_test")
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": True, "data": {"reference": "ref123", "status": "success"}}
    mock_response.raise_for_status = MagicMock()
    
    mock_client = MagicMock()
    async def aenter(*args, **kwargs):
        return mock_client
    async def aexit(*args, **kwargs):
        pass
    
    mock_client_context = MagicMock()
    mock_client_context.__aenter__ = aenter
    mock_client_context.__aexit__ = aexit
    
    mock_client.get = AsyncMock(return_value=mock_response)
    
    with patch("httpx.AsyncClient", return_value=mock_client_context):
        res = await gateway.verify_transaction("ref123")
        assert res["status"] is True
        mock_client.get.assert_called_once_with(
            "https://api.paystack.co/transaction/verify/ref123",
            headers=gateway.headers
        )

@pytest.mark.anyio
async def test_create_transfer_recipient():
    gateway = PaystackPaymentGateway(secret_key="sk_test")
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": True, "data": {"recipient_code": "RCP_123"}}
    mock_response.raise_for_status = MagicMock()
    
    mock_client = MagicMock()
    async def aenter(*args, **kwargs):
        return mock_client
    async def aexit(*args, **kwargs):
        pass
    
    mock_client_context = MagicMock()
    mock_client_context.__aenter__ = aenter
    mock_client_context.__aexit__ = aexit
    
    mock_client.post = AsyncMock(return_value=mock_response)
    
    with patch("httpx.AsyncClient", return_value=mock_client_context):
        res = await gateway.create_transfer_recipient(
            type="nuban",
            name="John Doe",
            account_number="0123456789",
            bank_code="058"
        )
        assert res["status"] is True
        mock_client.post.assert_called_once_with(
            "https://api.paystack.co/transferrecipient",
            json={
                "type": "nuban",
                "name": "John Doe",
                "account_number": "0123456789",
                "bank_code": "058",
                "currency": "NGN"
            },
            headers=gateway.headers
        )

@pytest.mark.anyio
async def test_initiate_transfer():
    gateway = PaystackPaymentGateway(secret_key="sk_test")
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": True, "data": {"transfer_code": "TRF_123"}}
    mock_response.raise_for_status = MagicMock()
    
    mock_client = MagicMock()
    async def aenter(*args, **kwargs):
        return mock_client
    async def aexit(*args, **kwargs):
        pass
    
    mock_client_context = MagicMock()
    mock_client_context.__aenter__ = aenter
    mock_client_context.__aexit__ = aexit
    
    mock_client.post = AsyncMock(return_value=mock_response)
    
    with patch("httpx.AsyncClient", return_value=mock_client_context):
        res = await gateway.initiate_transfer(
            amount=5000.0,
            recipient_code="RCP_123",
            reference="ref555",
            reason="Payout for cleaning job"
        )
        assert res["status"] is True
        mock_client.post.assert_called_once_with(
            "https://api.paystack.co/transfer",
            json={
                "source": "balance",
                "amount": 500000,  # 5000.0 * 100
                "recipient": "RCP_123",
                "reference": "ref555",
                "reason": "Payout for cleaning job"
            },
            headers=gateway.headers
        )

@pytest.mark.anyio
async def test_create_subaccount():
    gateway = PaystackPaymentGateway(secret_key="sk_test")
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": True, "data": {"subaccount_code": "ACCT_123"}}
    mock_response.raise_for_status = MagicMock()
    
    mock_client = MagicMock()
    async def aenter(*args, **kwargs):
        return mock_client
    async def aexit(*args, **kwargs):
        pass
    
    mock_client_context = MagicMock()
    mock_client_context.__aenter__ = aenter
    mock_client_context.__aexit__ = aexit
    
    mock_client.post = AsyncMock(return_value=mock_response)
    
    with patch("httpx.AsyncClient", return_value=mock_client_context):
        res = await gateway.create_subaccount(
            business_name="Cleaner Co",
            settlement_bank="058",
            account_number="0123456789",
            percentage_charge=1.5,
            description="Service provider subaccount",
            primary_contact_email="cleaner@co.com"
        )
        assert res["status"] is True
        mock_client.post.assert_called_once_with(
            "https://api.paystack.co/subaccount",
            json={
                "business_name": "Cleaner Co",
                "settlement_bank": "058",
                "account_number": "0123456789",
                "percentage_charge": 1.5,
                "description": "Service provider subaccount",
                "primary_contact_email": "cleaner@co.com"
            },
            headers=gateway.headers
        )

@pytest.mark.anyio
async def test_update_subaccount():
    gateway = PaystackPaymentGateway(secret_key="sk_test")
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": True, "data": {"subaccount_code": "ACCT_123", "active": False}}
    mock_response.raise_for_status = MagicMock()
    
    mock_client = MagicMock()
    async def aenter(*args, **kwargs):
        return mock_client
    async def aexit(*args, **kwargs):
        pass
    
    mock_client_context = MagicMock()
    mock_client_context.__aenter__ = aenter
    mock_client_context.__aexit__ = aexit
    
    mock_client.put = AsyncMock(return_value=mock_response)
    
    with patch("httpx.AsyncClient", return_value=mock_client_context):
        res = await gateway.update_subaccount(
            subaccount_code="ACCT_123",
            business_name="New Cleaner Co",
            active=False
        )
        assert res["status"] is True
        mock_client.put.assert_called_once_with(
            "https://api.paystack.co/subaccount/ACCT_123",
            json={
                "business_name": "New Cleaner Co",
                "active": False
            },
            headers=gateway.headers
        )

@pytest.mark.anyio
async def test_get_subaccount():
    gateway = PaystackPaymentGateway(secret_key="sk_test")
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": True, "data": {"subaccount_code": "ACCT_123"}}
    mock_response.raise_for_status = MagicMock()
    
    mock_client = MagicMock()
    async def aenter(*args, **kwargs):
        return mock_client
    async def aexit(*args, **kwargs):
        pass
    
    mock_client_context = MagicMock()
    mock_client_context.__aenter__ = aenter
    mock_client_context.__aexit__ = aexit
    
    mock_client.get = AsyncMock(return_value=mock_response)
    
    with patch("httpx.AsyncClient", return_value=mock_client_context):
        res = await gateway.get_subaccount("ACCT_123")
        assert res["status"] is True
        mock_client.get.assert_called_once_with(
            "https://api.paystack.co/subaccount/ACCT_123",
            headers=gateway.headers
        )
