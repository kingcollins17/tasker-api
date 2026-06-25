import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from app.core.deps.auth import GetCurrentUser
from app.features.users.services import UserService
from app.features.users.schemas import UserResponse
from app.core.models.users import User, UserType, KYCStatus, ProviderProfile
from app.core.utils import security


@pytest.mark.asyncio
async def test_get_current_user_success(monkeypatch):
    # Arrange
    user = User(
        id="user-123",
        email="test@example.com",
        phone_number="+2348012345678",
        type=UserType.CUSTOMER,
        is_active=True,
        email_verified=True,
        phone_verified=True,
    )
    
    # Mock JWT decode
    monkeypatch.setattr(security, "decode_access_token", lambda token: {"id": "user-123"})
    
    user_service = MagicMock(spec=UserService)
    user_service.get_user = AsyncMock(return_value=user)
    
    dep = GetCurrentUser(
        required_active=True,
        required_email_verified=True,
        required_phone_verified=True,
        require_phone_present=True,
    )
    
    # Act
    result = await dep(
        token_oauth="valid_token",
        token_bearer=None,
        user_service=user_service
    )
    
    # Assert
    assert isinstance(result, UserResponse)
    assert result.id == "user-123"
    assert result.email == "test@example.com"
    user_service.get_user.assert_called_once_with("user-123")


@pytest.mark.asyncio
async def test_get_current_user_oauth_vs_bearer(monkeypatch):
    user = User(
        id="user-123",
        email="test@example.com",
        type=UserType.CUSTOMER,
        is_active=True,
    )
    monkeypatch.setattr(security, "decode_access_token", lambda token: {"id": "user-123"})
    
    user_service = MagicMock(spec=UserService)
    user_service.get_user = AsyncMock(return_value=user)
    
    dep = GetCurrentUser(required_active=True)
    
    # Verify works with token_bearer
    bearer_cred = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token_value")
    result = await dep(
        token_oauth=None,
        token_bearer=bearer_cred,
        user_service=user_service
    )
    assert result.id == "user-123"


@pytest.mark.asyncio
async def test_get_current_user_no_token():
    dep = GetCurrentUser()
    with pytest.raises(HTTPException) as exc_info:
        await dep(token_oauth=None, token_bearer=None, user_service=MagicMock())
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "Not authenticated"


@pytest.mark.asyncio
async def test_get_current_user_invalid_token(monkeypatch):
    monkeypatch.setattr(security, "decode_access_token", lambda token: None)
    dep = GetCurrentUser()
    with pytest.raises(HTTPException) as exc_info:
        await dep(token_oauth="invalid", token_bearer=None, user_service=MagicMock())
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Could not validate credentials" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_current_user_no_id_in_payload(monkeypatch):
    monkeypatch.setattr(security, "decode_access_token", lambda token: {})
    dep = GetCurrentUser()
    with pytest.raises(HTTPException) as exc_info:
        await dep(token_oauth="invalid", token_bearer=None, user_service=MagicMock())
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Could not validate credentials" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_current_user_user_not_found(monkeypatch):
    monkeypatch.setattr(security, "decode_access_token", lambda token: {"id": "user-123"})
    user_service = MagicMock(spec=UserService)
    user_service.get_user = AsyncMock(return_value=None)
    dep = GetCurrentUser()
    with pytest.raises(HTTPException) as exc_info:
        await dep(token_oauth="valid", token_bearer=None, user_service=user_service)
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "User not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_current_user_inactive(monkeypatch):
    monkeypatch.setattr(security, "decode_access_token", lambda token: {"id": "user-123"})
    user = User(
        id="user-123",
        email="test@example.com",
        type=UserType.CUSTOMER,
        is_active=False
    )
    user_service = MagicMock(spec=UserService)
    user_service.get_user = AsyncMock(return_value=user)
    
    # default requires active
    dep = GetCurrentUser()
    with pytest.raises(HTTPException) as exc_info:
        await dep(token_oauth="valid", token_bearer=None, user_service=user_service)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "User account is inactive"


@pytest.mark.asyncio
async def test_get_current_user_email_unverified(monkeypatch):
    monkeypatch.setattr(security, "decode_access_token", lambda token: {"id": "user-123"})
    user = User(
        id="user-123",
        email="test@example.com",
        type=UserType.CUSTOMER,
        is_active=True,
        email_verified=False
    )
    user_service = MagicMock(spec=UserService)
    user_service.get_user = AsyncMock(return_value=user)
    
    dep = GetCurrentUser(required_email_verified=True)
    with pytest.raises(HTTPException) as exc_info:
        await dep(token_oauth="valid", token_bearer=None, user_service=user_service)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "Email address not verified"


@pytest.mark.asyncio
async def test_get_current_user_phone_missing(monkeypatch):
    monkeypatch.setattr(security, "decode_access_token", lambda token: {"id": "user-123"})
    user = User(
        id="user-123",
        email="test@example.com",
        type=UserType.CUSTOMER,
        is_active=True,
        phone_number=None
    )
    user_service = MagicMock(spec=UserService)
    user_service.get_user = AsyncMock(return_value=user)
    
    dep = GetCurrentUser(require_phone_present=True)
    with pytest.raises(HTTPException) as exc_info:
        await dep(token_oauth="valid", token_bearer=None, user_service=user_service)
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Phone number is required"


@pytest.mark.asyncio
async def test_get_current_user_phone_unverified(monkeypatch):
    monkeypatch.setattr(security, "decode_access_token", lambda token: {"id": "user-123"})
    user = User(
        id="user-123",
        email="test@example.com",
        type=UserType.CUSTOMER,
        is_active=True,
        phone_number="+2348012345678",
        phone_verified=False
    )
    user_service = MagicMock(spec=UserService)
    user_service.get_user = AsyncMock(return_value=user)
    
    dep = GetCurrentUser(required_phone_verified=True)
    with pytest.raises(HTTPException) as exc_info:
        await dep(token_oauth="valid", token_bearer=None, user_service=user_service)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "Phone number not verified"


@pytest.mark.asyncio
async def test_get_current_user_custom_exceptions(monkeypatch):
    monkeypatch.setattr(security, "decode_access_token", lambda token: {"id": "user-123"})
    user = User(
        id="user-123",
        email="test@example.com",
        type=UserType.CUSTOMER,
        is_active=False
    )
    user_service = MagicMock(spec=UserService)
    user_service.get_user = AsyncMock(return_value=user)
    
    dep = GetCurrentUser(
        required_active=True,
        active_error_status=status.HTTP_401_UNAUTHORIZED,
        active_error_detail="Custom Inactive Error Message"
    )
    with pytest.raises(HTTPException) as exc_info:
        await dep(token_oauth="valid", token_bearer=None, user_service=user_service)
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "Custom Inactive Error Message"


@pytest.mark.asyncio
async def test_get_current_user_type_restriction_success(monkeypatch):
    monkeypatch.setattr(security, "decode_access_token", lambda token: {"id": "user-123"})
    user = User(
        id="user-123",
        email="test@example.com",
        type=UserType.CUSTOMER,
        is_active=True
    )
    user_service = MagicMock(spec=UserService)
    user_service.get_user = AsyncMock(return_value=user)

    dep = GetCurrentUser(required_type=UserType.CUSTOMER)
    result = await dep(token_oauth="valid", token_bearer=None, user_service=user_service)
    assert result.type == UserType.CUSTOMER


@pytest.mark.asyncio
async def test_get_current_user_type_restriction_failure(monkeypatch):
    monkeypatch.setattr(security, "decode_access_token", lambda token: {"id": "user-123"})
    user = User(
        id="user-123",
        email="test@example.com",
        type=UserType.CUSTOMER,
        is_active=True
    )
    user_service = MagicMock(spec=UserService)
    user_service.get_user = AsyncMock(return_value=user)

    dep = GetCurrentUser(required_type=UserType.PROVIDER)
    with pytest.raises(HTTPException) as exc_info:
        await dep(token_oauth="valid", token_bearer=None, user_service=user_service)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "User type is not authorized"


@pytest.mark.asyncio
async def test_get_current_user_type_restriction_custom(monkeypatch):
    monkeypatch.setattr(security, "decode_access_token", lambda token: {"id": "user-123"})
    user = User(
        id="user-123",
        email="test@example.com",
        type=UserType.CUSTOMER,
        is_active=True
    )
    user_service = MagicMock(spec=UserService)
    user_service.get_user = AsyncMock(return_value=user)

    dep = GetCurrentUser(
        required_type=UserType.PROVIDER,
        type_error_status=status.HTTP_400_BAD_REQUEST,
        type_error_detail="Only providers are allowed here"
    )
    with pytest.raises(HTTPException) as exc_info:
        await dep(token_oauth="valid", token_bearer=None, user_service=user_service)
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Only providers are allowed here"


@pytest.mark.asyncio
async def test_get_current_user_kyc_status_success(monkeypatch):
    monkeypatch.setattr(security, "decode_access_token", lambda token: {"id": "user-123"})
    
    provider_profile = ProviderProfile(
        id="profile-123",
        user_id="user-123",
        status=KYCStatus.VERIFIED
    )
    user = User(
        id="user-123",
        email="provider@example.com",
        type=UserType.PROVIDER,
        is_active=True,
        provider_profile=provider_profile
    )
    user_service = MagicMock(spec=UserService)
    user_service.get_user = AsyncMock(return_value=user)

    dep = GetCurrentUser(allowed_kyc_statuses=[KYCStatus.VERIFIED])
    result = await dep(token_oauth="valid", token_bearer=None, user_service=user_service)
    assert result.id == "user-123"
    assert result.provider_profile is not None
    assert result.provider_profile.status == KYCStatus.VERIFIED


@pytest.mark.asyncio
async def test_get_current_user_kyc_status_missing_profile(monkeypatch):
    monkeypatch.setattr(security, "decode_access_token", lambda token: {"id": "user-123"})
    user = User(
        id="user-123",
        email="customer@example.com",
        type=UserType.CUSTOMER,
        is_active=True,
        provider_profile=None
    )
    user_service = MagicMock(spec=UserService)
    user_service.get_user = AsyncMock(return_value=user)

    dep = GetCurrentUser(allowed_kyc_statuses=[KYCStatus.VERIFIED])
    with pytest.raises(HTTPException) as exc_info:
        await dep(token_oauth="valid", token_bearer=None, user_service=user_service)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "KYC status requirement not met"


@pytest.mark.asyncio
async def test_get_current_user_kyc_status_mismatch(monkeypatch):
    monkeypatch.setattr(security, "decode_access_token", lambda token: {"id": "user-123"})
    provider_profile = ProviderProfile(
        id="profile-123",
        user_id="user-123",
        status=KYCStatus.PENDING_SUBMISSION
    )
    user = User(
        id="user-123",
        email="provider@example.com",
        type=UserType.PROVIDER,
        is_active=True,
        provider_profile=provider_profile
    )
    user_service = MagicMock(spec=UserService)
    user_service.get_user = AsyncMock(return_value=user)

    dep = GetCurrentUser(allowed_kyc_statuses=[KYCStatus.VERIFIED])
    with pytest.raises(HTTPException) as exc_info:
        await dep(token_oauth="valid", token_bearer=None, user_service=user_service)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "KYC status requirement not met"


@pytest.mark.asyncio
async def test_get_current_user_kyc_status_custom_error(monkeypatch):
    monkeypatch.setattr(security, "decode_access_token", lambda token: {"id": "user-123"})
    provider_profile = ProviderProfile(
        id="profile-123",
        user_id="user-123",
        status=KYCStatus.FAILED
    )
    user = User(
        id="user-123",
        email="provider@example.com",
        type=UserType.PROVIDER,
        is_active=True,
        provider_profile=provider_profile
    )
    user_service = MagicMock(spec=UserService)
    user_service.get_user = AsyncMock(return_value=user)

    dep = GetCurrentUser(
        allowed_kyc_statuses=[KYCStatus.VERIFIED],
        kyc_status_error_status=status.HTTP_400_BAD_REQUEST,
        kyc_status_error_detail="KYC verification is not complete or failed"
    )
    with pytest.raises(HTTPException) as exc_info:
        await dep(token_oauth="valid", token_bearer=None, user_service=user_service)
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "KYC verification is not complete or failed"

