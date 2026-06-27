import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from fastapi import status, HTTPException

from app.main import app
from app.features.users.services import get_user_service, UserService
from app.core.models.users import User, UserType
from app.core.repository import Repository
from app.core.utils import security
from app.features.users.schemas import UserLogin
from app.core.services import (
    OTPService,
    OTPRateLimitError,
    OTPVerificationError,
    OTPMaxAttemptsReachedError,
    OTPError,
)


@pytest.fixture
def mock_user_repo():
    repo = MagicMock(spec=Repository)
    repo.get_all = AsyncMock(return_value=[])
    repo.update = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_otp_service():
    otp = MagicMock(spec=OTPService)
    otp.generate_and_send_otp = AsyncMock(return_value=True)
    otp.verify_otp = AsyncMock(return_value=True)
    return otp


@pytest.fixture
def user_service(mock_user_repo, mock_otp_service):
    # customer_repo and provider_repo are not needed for OTP verification
    return UserService(
        user_repo=mock_user_repo,
        customer_repo=MagicMock(),
        provider_repo=MagicMock(),
        otp_service=mock_otp_service,
        region_repo=MagicMock(),
    )


@pytest.fixture
def client(user_service):
    # Override get_user_service dependency to inject our mocked service
    app.dependency_overrides[get_user_service] = lambda: user_service
    yield TestClient(app)
    # Clear overrides after test
    app.dependency_overrides.clear()


# ==========================================
# UserService Unit Tests
# ==========================================

@pytest.mark.asyncio
async def test_request_email_otp_success(user_service, mock_user_repo, mock_otp_service):
    # Arrange
    email = "test@example.com"
    mock_user = User(email=email, type=UserType.CUSTOMER, is_active=True)
    mock_user_repo.get_all.return_value = [mock_user]

    # Act
    await user_service.request_email_otp(email)

    # Assert
    mock_user_repo.get_all.assert_called_once()
    mock_otp_service.generate_and_send_otp.assert_called_once_with(target=email, channel="email")


@pytest.mark.asyncio
async def test_request_email_otp_user_not_found(user_service, mock_user_repo):
    # Arrange
    email = "nonexistent@example.com"
    mock_user_repo.get_all.return_value = []

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await user_service.request_email_otp(email)
    
    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert "does not exist" in exc_info.value.detail


@pytest.mark.asyncio
async def test_request_email_otp_rate_limit(user_service, mock_user_repo, mock_otp_service):
    # Arrange
    email = "test@example.com"
    mock_user = User(email=email, type=UserType.CUSTOMER, is_active=True)
    mock_user_repo.get_all.return_value = [mock_user]
    mock_otp_service.generate_and_send_otp.side_effect = OTPRateLimitError("Please wait.")

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await user_service.request_email_otp(email)
    
    assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert "Please wait" in exc_info.value.detail


@pytest.mark.asyncio
async def test_verify_email_otp_success(user_service, mock_user_repo, mock_otp_service):
    # Arrange
    email = "test@example.com"
    code = "123456"
    mock_user = User(id="user-123", email=email, type=UserType.CUSTOMER, is_active=True, email_verified=False)
    mock_user_repo.get_all.return_value = [mock_user]
    
    updated_user = User(id="user-123", email=email, type=UserType.CUSTOMER, is_active=True, email_verified=True)
    mock_user_repo.update.return_value = updated_user

    # Act
    result = await user_service.verify_email_otp(email, code)

    # Assert
    mock_otp_service.verify_otp.assert_called_once_with(target=email, channel="email", code=code)
    mock_user_repo.update.assert_called_once_with("user-123", {"email_verified": True})
    assert result.email_verified is True


@pytest.mark.asyncio
async def test_verify_email_otp_invalid_code(user_service, mock_user_repo, mock_otp_service):
    # Arrange
    email = "test@example.com"
    code = "111111"
    mock_user = User(id="user-123", email=email, type=UserType.CUSTOMER, is_active=True)
    mock_user_repo.get_all.return_value = [mock_user]
    mock_otp_service.verify_otp.return_value = False

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await user_service.verify_email_otp(email, code)
    
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid verification code" in exc_info.value.detail


# ==========================================
# Router API Integration Tests
# ==========================================

def test_api_request_email_otp_success(client, user_service):
    # Arrange
    user_service.request_email_otp = AsyncMock(return_value=None)

    # Act
    response = client.post("/api/v1/users/request-email-otp", json={"email": "test@example.com"})

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["detail"] == "Verification code sent to your email."
    user_service.request_email_otp.assert_called_once_with("test@example.com")


def test_api_request_email_otp_invalid_email(client):
    # Act
    response = client.post("/api/v1/users/request-email-otp", json={"email": "not-an-email"})

    # Assert
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_api_verify_email_success(client, user_service):
    # Arrange
    mock_user = User(
        id="user-123",
        email="test@example.com",
        type=UserType.CUSTOMER,
        is_active=True,
        email_verified=True,
        phone_verified=False
    )
    user_service.verify_email_otp = AsyncMock(return_value=mock_user)

    # Act
    response = client.post("/api/v1/users/verify-email", json={"email": "test@example.com", "code": "123456"})

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["detail"] == "Email verified successfully."
    assert data["data"]["email_verified"] is True
    user_service.verify_email_otp.assert_called_once_with("test@example.com", "123456")


def test_api_request_phone_otp_success(client, user_service):
    # Arrange
    user_service.request_phone_otp = AsyncMock(return_value=None)

    # Act
    response = client.post("/api/v1/users/request-phone-otp", json={"phone_number": "08031234567"})

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["detail"] == "Verification code sent to your phone number."
    user_service.request_phone_otp.assert_called_once_with("+2348031234567")


def test_api_verify_phone_success(client, user_service):
    # Arrange
    mock_user = User(
        id="user-123",
        email="test@example.com",
        phone_number="+2348031234567",
        type=UserType.CUSTOMER,
        is_active=True,
        email_verified=False,
        phone_verified=True
    )
    user_service.verify_phone_otp = AsyncMock(return_value=mock_user)

    # Act
    response = client.post("/api/v1/users/verify-phone", json={"phone_number": "08031234567", "code": "123456"})

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["detail"] == "Phone number verified successfully."
    assert data["data"]["phone_verified"] is True
    user_service.verify_phone_otp.assert_called_once_with("+2348031234567", "123456")


# ==========================================
# Login Unit & Integration Tests
# ==========================================

@pytest.mark.asyncio
async def test_login_user_success(user_service, mock_user_repo):
    # Arrange
    email = "test@example.com"
    password = "secretpassword"
    hashed = security.hash_password(password)
    mock_user = User(id="user-123", email=email, hashed_password=hashed, type=UserType.CUSTOMER, is_active=True)
    mock_user_repo.get_all.return_value = [mock_user]

    schema = UserLogin(email=email, password=password)

    # Act
    res = await user_service.login_user(schema)

    # Assert
    assert res["access_token"] is not None
    assert res["token_type"] == "bearer"
    assert res["user"].id == "user-123"


@pytest.mark.asyncio
async def test_login_user_invalid_email(user_service, mock_user_repo):
    # Arrange
    mock_user_repo.get_all.return_value = []
    schema = UserLogin(email="nonexistent@example.com", password="password")

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await user_service.login_user(schema)
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Invalid email or password" in exc_info.value.detail


@pytest.mark.asyncio
async def test_login_user_invalid_password(user_service, mock_user_repo):
    # Arrange
    email = "test@example.com"
    password = "correctpassword"
    hashed = security.hash_password(password)
    mock_user = User(id="user-123", email=email, hashed_password=hashed, type=UserType.CUSTOMER, is_active=True)
    mock_user_repo.get_all.return_value = [mock_user]

    schema = UserLogin(email=email, password="wrongpassword")

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await user_service.login_user(schema)
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Invalid email or password" in exc_info.value.detail


@pytest.mark.asyncio
async def test_login_user_inactive(user_service, mock_user_repo):
    # Arrange
    email = "test@example.com"
    password = "password"
    hashed = security.hash_password(password)
    mock_user = User(id="user-123", email=email, hashed_password=hashed, type=UserType.CUSTOMER, is_active=False)
    mock_user_repo.get_all.return_value = [mock_user]

    schema = UserLogin(email=email, password=password)

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await user_service.login_user(schema)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert "inactive" in exc_info.value.detail


def test_api_login_success(client, user_service):
    # Arrange
    mock_user = User(
        id="user-123",
        email="test@example.com",
        type=UserType.CUSTOMER,
        is_active=True,
        email_verified=True,
        phone_verified=False
    )
    user_service.login_user = AsyncMock(return_value={
        "access_token": "mock-jwt-token",
        "token_type": "bearer",
        "user": mock_user
    })

    # Act
    response = client.post("/api/v1/users/login", data={"username": "test@example.com", "password": "password123"})

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["access_token"] == "mock-jwt-token"
    assert data["token_type"] == "bearer"
    assert data["user"]["id"] == "user-123"


def test_api_get_me_success(client, user_service):
    # Arrange
    mock_user = User(
        id="user-123",
        email="test@example.com",
        type=UserType.CUSTOMER,
        is_active=True,
        email_verified=True,
        phone_verified=False
    )
    user_service.get_user = AsyncMock(return_value=mock_user)

    from app.core.utils import security
    original_decode = security.decode_access_token
    security.decode_access_token = MagicMock(return_value={"id": "user-123"})
    try:
        # Act
        response = client.get("/api/v1/users/me", headers={"Authorization": "Bearer mock-token"})
    finally:
        security.decode_access_token = original_decode

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["detail"] == "User profile retrieved successfully."
    assert data["data"]["id"] == "user-123"
    user_service.get_user.assert_called_once_with("user-123")


def test_api_get_me_unauthorized(client):
    # Act
    response = client.get("/api/v1/users/me")

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_api_get_me_customer_profile(client, user_service):
    # Arrange
    from app.core.models.users import CustomerProfile
    mock_customer_profile = CustomerProfile(
        id="cust-123",
        user_id="user-123",
        first_name="John",
        last_name="Doe"
    )
    mock_user = User(
        id="user-123",
        email="test@example.com",
        type=UserType.CUSTOMER,
        is_active=True,
        email_verified=True,
        phone_verified=False,
        customer_profile=mock_customer_profile
    )
    user_service.get_user = AsyncMock(return_value=mock_user)

    from app.core.utils import security
    original_decode = security.decode_access_token
    security.decode_access_token = MagicMock(return_value={"id": "user-123"})
    try:
        response = client.get("/api/v1/users/me", headers={"Authorization": "Bearer mock-token"})
    finally:
        security.decode_access_token = original_decode

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["data"]["id"] == "user-123"
    assert data["data"]["customer_profile"]["id"] == "cust-123"
    assert data["data"]["customer_profile"]["first_name"] == "John"
    assert data["data"]["customer_profile"]["last_name"] == "Doe"
    assert data["data"]["provider_profile"] is None


def test_api_get_me_provider_profile(client, user_service):
    # Arrange
    from app.core.models.users import ProviderProfile, KYCStatus
    mock_provider_profile = ProviderProfile(
        id="prov-123",
        user_id="user-123",
        first_name="Jane",
        last_name="Smith",
        status=KYCStatus.VERIFIED
    )
    mock_user = User(
        id="user-123",
        email="test@example.com",
        type=UserType.PROVIDER,
        is_active=True,
        email_verified=True,
        phone_verified=False,
        provider_profile=mock_provider_profile
    )
    user_service.get_user = AsyncMock(return_value=mock_user)

    from app.core.utils import security
    original_decode = security.decode_access_token
    security.decode_access_token = MagicMock(return_value={"id": "user-123"})
    try:
        response = client.get("/api/v1/users/me", headers={"Authorization": "Bearer mock-token"})
    finally:
        security.decode_access_token = original_decode

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["data"]["id"] == "user-123"
    assert data["data"]["provider_profile"]["id"] == "prov-123"
    assert data["data"]["provider_profile"]["first_name"] == "Jane"
    assert data["data"]["provider_profile"]["last_name"] == "Smith"
    assert data["data"]["provider_profile"]["status"] == "verified"
    assert data["data"]["customer_profile"] is None


# ==========================================
# UserService.verify_otp Unit Tests
# ==========================================

@pytest.mark.asyncio
async def test_service_verify_otp_email_success(user_service, mock_otp_service):
    # Arrange
    target = "test@example.com"
    channel = "email"
    code = "123456"
    mock_otp_service.verify_otp.return_value = True

    # Act
    res = await user_service.verify_otp(target, channel, code)

    # Assert
    assert res is True
    mock_otp_service.verify_otp.assert_called_once_with(target=target, channel=channel, code=code)


@pytest.mark.asyncio
async def test_service_verify_otp_sms_success(user_service, mock_otp_service):
    # Arrange
    target = "08031234567"
    channel = "sms"
    code = "123456"
    mock_otp_service.verify_otp.return_value = True

    # Act
    res = await user_service.verify_otp(target, channel, code)

    # Assert
    assert res is True
    mock_otp_service.verify_otp.assert_called_once_with(target="+2348031234567", channel=channel, code=code)


@pytest.mark.asyncio
async def test_service_verify_otp_invalid_code(user_service, mock_otp_service):
    # Arrange
    target = "test@example.com"
    channel = "email"
    code = "111111"
    mock_otp_service.verify_otp.return_value = False

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await user_service.verify_otp(target, channel, code)
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid verification code" in exc_info.value.detail


# ==========================================
# Generic Verify OTP Route API Tests
# ==========================================

def test_api_verify_otp_success(client, user_service):
    # Arrange
    user_service.verify_otp = AsyncMock(return_value=True)

    # Act
    response = client.post(
        "/api/v1/users/verify-otp",
        json={"target": "test@example.com", "channel": "email", "code": "123456"}
    )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["data"] is True
    assert data["detail"] == "OTP verified successfully."
    user_service.verify_otp.assert_called_once_with("test@example.com", "email", "123456")

