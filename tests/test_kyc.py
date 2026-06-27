import io
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from fastapi import status, HTTPException

from app.main import app
from app.features.users.services import get_user_service, UserService
from app.core.services.storage import get_storage_service, StorageService
from app.core.models.users import User, UserType, ProviderProfile, KYCStatus
from app.core.repository import Repository
from app.core.utils import security


@pytest.fixture
def mock_user_repo():
    repo = MagicMock(spec=Repository)
    repo.get_all = AsyncMock(return_value=[])
    repo.update = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_provider_repo():
    repo = MagicMock(spec=Repository)
    repo.get_all = AsyncMock(return_value=[])
    repo.update = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_region_repo():
    repo = MagicMock(spec=Repository)
    repo.get = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_storage_service():
    service = MagicMock(spec=StorageService)
    service.upload_file = AsyncMock(side_effect=lambda file, filename=None: f"https://mock-storage.local/{getattr(file, 'filename', 'uploaded_file')}")
    return service


@pytest.fixture
def user_service(mock_user_repo, mock_provider_repo, mock_region_repo):
    return UserService(
        user_repo=mock_user_repo,
        customer_repo=MagicMock(),
        provider_repo=mock_provider_repo,
        otp_service=MagicMock(),
        region_repo=mock_region_repo,
    )


@pytest.fixture
def client(user_service, mock_storage_service):
    app.dependency_overrides[get_user_service] = lambda: user_service
    app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
    yield TestClient(app)
    app.dependency_overrides.clear()


# ==========================================
# UserService.submit_kyc Unit Tests
# ==========================================

@pytest.mark.asyncio
async def test_submit_kyc_success(user_service, mock_provider_repo):
    # Arrange
    user_id = "provider-123"
    profile = ProviderProfile(
        id="profile-123",
        user_id=user_id,
        first_name="John",
        last_name="Doe",
        status=KYCStatus.PENDING_SUBMISSION
    )
    mock_provider_repo.get_all.return_value = [profile]
    
    updated_profile = ProviderProfile(
        id="profile-123",
        user_id=user_id,
        first_name="John",
        last_name="Doe",
        id_type="NIN",
        id_number="1234567890",
        id_doc_url="https://mock-storage.local/id.png",
        selfie_url="https://mock-storage.local/selfie.png",
        status=KYCStatus.SUBMITTED
    )
    mock_provider_repo.update.return_value = updated_profile

    # Act
    res = await user_service.submit_kyc(
        user_id=user_id,
        id_type="NIN",
        id_number="1234567890",
        id_doc_url="https://mock-storage.local/id.png",
        selfie_url="https://mock-storage.local/selfie.png"
    )

    # Assert
    assert res.status == KYCStatus.SUBMITTED
    assert res.id_type == "NIN"
    assert res.id_number == "1234567890"
    mock_provider_repo.get_all.assert_called_once()
    mock_provider_repo.update.assert_called_once()


# ==========================================
# KYC Router API Tests
# ==========================================

def test_api_submit_kyc_provider_success(client, user_service, mock_storage_service):
    # Arrange
    user_id = "provider-123"
    # Create a real token
    token = security.create_access_token({"id": user_id})
    
    provider_profile = ProviderProfile(
        id="profile-123",
        user_id=user_id,
        first_name="John",
        last_name="Doe",
        status=KYCStatus.PENDING_SUBMISSION
    )
    
    # Mock user returned by security dependency
    mock_user = User(
        id=user_id,
        email="provider@example.com",
        type=UserType.PROVIDER,
        is_active=True,
        email_verified=True,
        phone_verified=True,
        provider_profile=provider_profile
    )
    user_service.get_user = AsyncMock(return_value=mock_user)
    
    updated_profile = ProviderProfile(
        id="profile-123",
        user_id=user_id,
        first_name="John",
        last_name="Doe",
        id_type="NIN",
        id_number="1234567890",
        id_doc_url="https://mock-storage.local/id.png",
        selfie_url="https://mock-storage.local/selfie.png",
        status=KYCStatus.SUBMITTED
    )
    user_service.submit_kyc = AsyncMock(return_value=updated_profile)

    # Prepare files
    id_doc_file = ("id.png", io.BytesIO(b"fake doc"), "image/png")
    selfie_file = ("selfie.png", io.BytesIO(b"fake selfie"), "image/png")

    # Act
    response = client.post(
        "/api/v1/users/kyc",
        data={"id_type": "NIN", "id_number": "1234567890"},
        files={"id_doc": id_doc_file, "selfie": selfie_file},
        headers={"Authorization": f"Bearer {token}"}
    )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    res_data = response.json()
    assert res_data["detail"] == "KYC documents submitted successfully."
    assert res_data["data"]["status"] == KYCStatus.SUBMITTED
    user_service.submit_kyc.assert_called_once_with(
        user_id=user_id,
        id_type="NIN",
        id_number="1234567890",
        id_doc_url="https://mock-storage.local/id.png",
        selfie_url="https://mock-storage.local/selfie.png"
    )


def test_api_submit_kyc_customer_forbidden(client, user_service):
    # Arrange
    user_id = "customer-123"
    token = security.create_access_token({"id": user_id})
    
    # Mock user as customer
    mock_user = User(
        id=user_id,
        email="customer@example.com",
        type=UserType.CUSTOMER,
        is_active=True,
        email_verified=True,
        phone_verified=True
    )
    user_service.get_user = AsyncMock(return_value=mock_user)

    # Prepare files
    id_doc_file = ("id.png", io.BytesIO(b"fake doc"), "image/png")
    selfie_file = ("selfie.png", io.BytesIO(b"fake selfie"), "image/png")

    # Act
    response = client.post(
        "/api/v1/users/kyc",
        data={"id_type": "NIN", "id_number": "1234567890"},
        files={"id_doc": id_doc_file, "selfie": selfie_file},
        headers={"Authorization": f"Bearer {token}"}
    )

    # Assert
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "not authorized" in response.json()["detail"]


def test_api_submit_kyc_email_not_verified(client, user_service):
    user_id = "provider-123"
    token = security.create_access_token({"id": user_id})
    provider_profile = ProviderProfile(
        id="profile-123",
        user_id=user_id,
        status=KYCStatus.PENDING_SUBMISSION
    )
    mock_user = User(
        id=user_id,
        email="provider@example.com",
        type=UserType.PROVIDER,
        is_active=True,
        email_verified=False,
        phone_verified=True,
        provider_profile=provider_profile
    )
    user_service.get_user = AsyncMock(return_value=mock_user)
    id_doc_file = ("id.png", io.BytesIO(b"fake doc"), "image/png")
    selfie_file = ("selfie.png", io.BytesIO(b"fake selfie"), "image/png")

    response = client.post(
        "/api/v1/users/kyc",
        data={"id_type": "NIN", "id_number": "1234567890"},
        files={"id_doc": id_doc_file, "selfie": selfie_file},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "Email address not verified" in response.json()["detail"]


def test_api_submit_kyc_phone_not_verified(client, user_service):
    user_id = "provider-123"
    token = security.create_access_token({"id": user_id})
    provider_profile = ProviderProfile(
        id="profile-123",
        user_id=user_id,
        status=KYCStatus.PENDING_SUBMISSION
    )
    mock_user = User(
        id=user_id,
        email="provider@example.com",
        type=UserType.PROVIDER,
        is_active=True,
        email_verified=True,
        phone_verified=False,
        provider_profile=provider_profile
    )
    user_service.get_user = AsyncMock(return_value=mock_user)
    id_doc_file = ("id.png", io.BytesIO(b"fake doc"), "image/png")
    selfie_file = ("selfie.png", io.BytesIO(b"fake selfie"), "image/png")

    response = client.post(
        "/api/v1/users/kyc",
        data={"id_type": "NIN", "id_number": "1234567890"},
        files={"id_doc": id_doc_file, "selfie": selfie_file},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "Phone number not verified" in response.json()["detail"]


def test_api_submit_kyc_status_not_allowed(client, user_service):
    user_id = "provider-123"
    token = security.create_access_token({"id": user_id})
    provider_profile = ProviderProfile(
        id="profile-123",
        user_id=user_id,
        status=KYCStatus.SUBMITTED
    )
    mock_user = User(
        id=user_id,
        email="provider@example.com",
        type=UserType.PROVIDER,
        is_active=True,
        email_verified=True,
        phone_verified=True,
        provider_profile=provider_profile
    )
    user_service.get_user = AsyncMock(return_value=mock_user)
    id_doc_file = ("id.png", io.BytesIO(b"fake doc"), "image/png")
    selfie_file = ("selfie.png", io.BytesIO(b"fake selfie"), "image/png")

    response = client.post(
        "/api/v1/users/kyc",
        data={"id_type": "NIN", "id_number": "1234567890"},
        files={"id_doc": id_doc_file, "selfie": selfie_file},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "KYC status requirement not met" in response.json()["detail"]


def test_api_get_kyc_status_success(client, user_service):
    # Arrange
    user_id = "provider-123"
    token = security.create_access_token({"id": user_id})
    
    provider_profile = ProviderProfile(
        id="profile-123",
        user_id=user_id,
        first_name="John",
        last_name="Doe",
        id_type="NIN",
        id_number="1234567890",
        id_doc_url="https://mock-storage.local/id.png",
        selfie_url="https://mock-storage.local/selfie.png",
        status=KYCStatus.SUBMITTED
    )
    
    # Mock user
    mock_user = User(
        id=user_id,
        email="provider@example.com",
        type=UserType.PROVIDER,
        is_active=True,
        provider_profile=provider_profile
    )
    user_service.get_user = AsyncMock(return_value=mock_user)

    # Act
    response = client.get(
        "/api/v1/users/kyc",
        headers={"Authorization": f"Bearer {token}"}
    )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    res_data = response.json()
    assert res_data["detail"] == "KYC details retrieved successfully."
    assert res_data["data"]["status"] == KYCStatus.SUBMITTED


@pytest.mark.asyncio
async def test_register_provider_with_gender(mock_user_repo, mock_provider_repo):
    # Arrange
    from app.features.users.schemas import UserRegister
    user_service = UserService(
        user_repo=mock_user_repo,
        customer_repo=MagicMock(),
        provider_repo=mock_provider_repo,
        otp_service=MagicMock(),
        region_repo=MagicMock(),
    )
    
    schema = UserRegister(
        email="provider@example.com",
        password="securepassword",
        type=UserType.PROVIDER,
        first_name="Jane",
        last_name="Doe",
        gender="female"
    )
    
    mock_user_repo.get_all.return_value = []
    
    # Mocking add and session
    mock_user_repo.session = MagicMock()
    mock_user_repo.session.refresh = AsyncMock()
    
    mock_user = User(
        id="user-123",
        email=schema.email,
        type=schema.type,
        is_active=True
    )
    mock_user_repo.add.return_value = mock_user
    
    # Act
    await user_service.register_user(schema)
    
    # Assert
    mock_provider_repo.add.assert_called_once()
    added_profile = mock_provider_repo.add.call_args[0][0]
    assert added_profile.gender == "female"
    assert added_profile.first_name == "Jane"
    assert added_profile.last_name == "Doe"


@pytest.mark.asyncio
async def test_register_user_with_valid_region(mock_user_repo, mock_provider_repo, mock_region_repo):
    # Arrange
    from app.features.users.schemas import UserRegister
    user_service = UserService(
        user_repo=mock_user_repo,
        customer_repo=MagicMock(),
        provider_repo=mock_provider_repo,
        otp_service=MagicMock(),
        region_repo=mock_region_repo,
    )
    
    schema = UserRegister(
        email="provider@example.com",
        password="securepassword",
        type=UserType.PROVIDER,
        first_name="Jane",
        last_name="Doe",
        region_id="region-123"
    )
    
    mock_user_repo.get_all.return_value = []
    
    # Mock region existence check via region_repo.get
    mock_region = MagicMock()
    mock_region.is_active = True
    mock_region_repo.get.return_value = mock_region
    
    # Mocking add and session
    mock_user_repo.session = MagicMock()
    mock_user_repo.session.refresh = AsyncMock()
    mock_provider_repo.add = AsyncMock()
    
    mock_user = User(
        id="user-123",
        email=schema.email,
        type=schema.type,
        is_active=True,
        region_id=schema.region_id
    )
    mock_user_repo.add.return_value = mock_user
    
    # Act
    res = await user_service.register_user(schema)
    
    # Assert
    mock_region_repo.get.assert_called_once_with("region-123")
    assert res.region_id == "region-123"


@pytest.mark.asyncio
async def test_register_user_with_invalid_region(mock_user_repo, mock_region_repo):
    # Arrange
    from app.features.users.schemas import UserRegister
    user_service = UserService(
        user_repo=mock_user_repo,
        customer_repo=MagicMock(),
        provider_repo=MagicMock(),
        otp_service=MagicMock(),
        region_repo=mock_region_repo,
    )
    
    schema = UserRegister(
        email="provider@example.com",
        password="securepassword",
        type=UserType.PROVIDER,
        first_name="Jane",
        last_name="Doe",
        region_id="invalid-region"
    )
    
    mock_user_repo.get_all.return_value = []
    
    # Mock region not found via region_repo.get
    mock_region_repo.get.return_value = None
    
    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await user_service.register_user(schema)
        
    mock_region_repo.get.assert_called_once_with("invalid-region")
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "The specified region does not exist."


@pytest.mark.asyncio
async def test_register_user_with_inactive_region(mock_user_repo, mock_region_repo):
    # Arrange
    from app.features.users.schemas import UserRegister
    user_service = UserService(
        user_repo=mock_user_repo,
        customer_repo=MagicMock(),
        provider_repo=MagicMock(),
        otp_service=MagicMock(),
        region_repo=mock_region_repo,
    )
    
    schema = UserRegister(
        email="provider@example.com",
        password="securepassword",
        type=UserType.PROVIDER,
        first_name="Jane",
        last_name="Doe",
        region_id="inactive-region"
    )
    
    mock_user_repo.get_all.return_value = []
    
    # Mock region found but inactive
    mock_region = MagicMock()
    mock_region.is_active = False
    mock_region_repo.get.return_value = mock_region
    
    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await user_service.register_user(schema)
        
    mock_region_repo.get.assert_called_once_with("inactive-region")
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "We are not active in this region yet"


# ==========================================
# UserService.update_provider_profile Unit Tests
# ==========================================

@pytest.mark.asyncio
async def test_update_provider_profile_success(user_service, mock_user_repo, mock_provider_repo):
    # Arrange
    user_id = "provider-123"
    
    # Mocking provider profile retrieval
    existing_profile = ProviderProfile(
        id="profile-123",
        user_id=user_id,
        first_name="Jane",
        last_name="Doe",
        gender="female"
    )
    mock_provider_repo.get_all.return_value = [existing_profile]
    
    # Mocking repos
    mock_provider_repo.update.return_value = existing_profile
    mock_user = User(
        id=user_id,
        email="provider@example.com",
        phone_number="+2348011112222",
        type=UserType.PROVIDER,
        is_active=True
    )
    mock_user_repo.get.return_value = mock_user
    mock_user_repo.session = MagicMock()
    mock_user_repo.session.refresh = AsyncMock()

    # Act
    res = await user_service.update_provider_profile(
        user_id=user_id,
        first_name="Janet",
        last_name="Smith",
        gender="other",
        phone_number="08022223333"
    )

    # Assert
    assert res.id == user_id
    mock_provider_repo.update.assert_called_once()
    call_args = mock_provider_repo.update.call_args[0]
    assert call_args[0] == "profile-123"
    assert call_args[1]["first_name"] == "Janet"
    assert call_args[1]["last_name"] == "Smith"
    assert call_args[1]["gender"] == "other"
    assert "updated_at" in call_args[1]
    mock_user_repo.update.assert_called_once_with(user_id, {"phone_number": "+2348022223333"})


@pytest.mark.asyncio
async def test_update_provider_profile_phone_exists(user_service, mock_user_repo):
    # Arrange
    user_id = "provider-123"
    
    # Mock check uniqueness: another user has the phone number
    another_user = User(id="other-user-456", email="other@example.com", phone_number="+2348022223333")
    mock_user_repo.get_all.return_value = [another_user]

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await user_service.update_provider_profile(
            user_id=user_id,
            phone_number="08022223333"
        )
    
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "already exists" in exc_info.value.detail


# ==========================================
# Update Provider Profile API Tests
# ==========================================

def test_api_update_profile_provider_success(client, user_service):
    # Arrange
    user_id = "provider-123"
    token = security.create_access_token({"id": user_id})
    
    # Mock user details returned by dependency
    provider_profile = ProviderProfile(
        id="profile-123",
        user_id=user_id,
        first_name="Jane",
        last_name="Doe",
        gender="female"
    )
    mock_user = User(
        id=user_id,
        email="provider@example.com",
        type=UserType.PROVIDER,
        is_active=True,
        provider_profile=provider_profile
    )
    user_service.get_user = AsyncMock(return_value=mock_user)
    
    # Mock update method
    updated_user = User(
        id=user_id,
        email="provider@example.com",
        phone_number="+2348022223333",
        type=UserType.PROVIDER,
        is_active=True,
        provider_profile=ProviderProfile(
            id="profile-123",
            user_id=user_id,
            first_name="Janet",
            last_name="Smith",
            gender="other"
        )
    )
    user_service.update_provider_profile = AsyncMock(return_value=updated_user)

    # Act
    response = client.put(
        "/api/v1/users/update-provider-profile",
        json={
            "first_name": "Janet",
            "last_name": "Smith",
            "gender": "other",
            "phone_number": "08022223333"
        },
        headers={"Authorization": f"Bearer {token}"}
    )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    res_data = response.json()
    assert res_data["detail"] == "Provider profile updated successfully."
    assert res_data["data"]["provider_profile"]["first_name"] == "Janet"
    assert res_data["data"]["provider_profile"]["gender"] == "other"
    assert res_data["data"]["phone_number"] == "+2348022223333"
    user_service.update_provider_profile.assert_called_once_with(
        user_id=user_id,
        first_name="Janet",
        last_name="Smith",
        gender="other",
        phone_number="+2348022223333"
    )


# ==========================================
# UserService.update_customer_profile Unit Tests
# ==========================================

@pytest.mark.asyncio
async def test_update_customer_profile_success(user_service, mock_user_repo, mock_provider_repo):
    # Arrange
    user_id = "customer-123"
    from app.core.models.users import CustomerProfile
    customer_repo = MagicMock(spec=Repository)
    user_service.customer_repo = customer_repo
    
    # Mocking customer profile retrieval
    existing_profile = CustomerProfile(
        id="profile-123",
        user_id=user_id,
        first_name="Jane",
        last_name="Doe"
    )
    customer_repo.get_all.return_value = [existing_profile]
    customer_repo.update.return_value = existing_profile
    
    mock_user = User(
        id=user_id,
        email="customer@example.com",
        type=UserType.CUSTOMER,
        is_active=True
    )
    mock_user_repo.get.return_value = mock_user
    mock_user_repo.session = MagicMock()
    mock_user_repo.session.refresh = AsyncMock()

    # Act
    res = await user_service.update_customer_profile(
        user_id=user_id,
        first_name="Janet",
        last_name="Smith"
    )

    # Assert
    assert res.id == user_id
    customer_repo.update.assert_called_once()
    call_args = customer_repo.update.call_args[0]
    assert call_args[0] == "profile-123"
    assert call_args[1]["first_name"] == "Janet"
    assert call_args[1]["last_name"] == "Smith"
    assert "updated_at" in call_args[1]


# ==========================================
# Update Seeker Profile API Tests
# ==========================================

def test_api_update_seeker_profile_success(client, user_service):
    # Arrange
    user_id = "customer-123"
    token = security.create_access_token({"id": user_id})
    from app.core.models.users import CustomerProfile
    
    # Mock user details returned by dependency
    customer_profile = CustomerProfile(
        id="profile-123",
        user_id=user_id,
        first_name="Jane",
        last_name="Doe"
    )
    mock_user = User(
        id=user_id,
        email="customer@example.com",
        type=UserType.CUSTOMER,
        is_active=True,
        customer_profile=customer_profile
    )
    user_service.get_user = AsyncMock(return_value=mock_user)
    
    # Mock update method
    updated_user = User(
        id=user_id,
        email="customer@example.com",
        type=UserType.CUSTOMER,
        is_active=True,
        customer_profile=CustomerProfile(
            id="profile-123",
            user_id=user_id,
            first_name="Janet",
            last_name="Smith"
        )
    )
    user_service.update_customer_profile = AsyncMock(return_value=updated_user)

    # Act
    response = client.put(
        "/api/v1/users/update-seeker-profile",
        json={
            "first_name": "Janet",
            "last_name": "Smith"
        },
        headers={"Authorization": f"Bearer {token}"}
    )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    res_data = response.json()
    assert res_data["detail"] == "Seeker profile updated successfully."
    assert res_data["data"]["customer_profile"]["first_name"] == "Janet"
    user_service.update_customer_profile.assert_called_once_with(
        user_id=user_id,
        first_name="Janet",
        last_name="Smith"
    )


# ==========================================
# UserService.update_user_location Unit Tests
# ==========================================

@pytest.mark.asyncio
async def test_update_user_location_customer_success(user_service, mock_user_repo):
    # Arrange
    user_id = "customer-123"
    from app.core.models.users import CustomerProfile
    customer_repo = MagicMock(spec=Repository)
    user_service.customer_repo = customer_repo

    existing_profile = CustomerProfile(id="profile-123", user_id=user_id)
    customer_repo.get_all.return_value = [existing_profile]

    # Act
    await user_service.update_user_location(
        user_id=user_id,
        user_type=UserType.CUSTOMER,
        latitude=6.5244,
        longitude=3.3792,
        address_line="123 Main St"
    )

    # Assert
    customer_repo.get_all.assert_called_once()
    customer_repo.update.assert_called_once()
    call_args = customer_repo.update.call_args[0]
    assert call_args[0] == "profile-123"
    assert call_args[1]["last_known_location"] == "POINT(3.3792 6.5244)"
    assert call_args[1]["address_line"] == "123 Main St"


# ==========================================
# Update Location API Tests
# ==========================================

def test_api_update_location_success(client, user_service):
    # Arrange
    user_id = "provider-123"
    token = security.create_access_token({"id": user_id})
    mock_user = User(id=user_id, email="provider@example.com", type=UserType.PROVIDER, is_active=True)
    user_service.get_user = AsyncMock(return_value=mock_user)
    user_service.update_user_location = AsyncMock()

    # Act
    response = client.put(
        "/api/v1/users/location",
        json={"latitude": 6.5244, "longitude": 3.3792, "address_line": "123 Main St"},
        headers={"Authorization": f"Bearer {token}"}
    )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["detail"] == "Location updated successfully."
    user_service.update_user_location.assert_called_once_with(
        user_id=user_id,
        user_type=UserType.PROVIDER,
        latitude=6.5244,
        longitude=3.3792,
        address_line="123 Main St"
    )


# ==========================================
# UserService.update_cloud_messaging_token Unit Tests
# ==========================================

@pytest.mark.asyncio
async def test_update_cloud_messaging_token_success(user_service, mock_user_repo):
    # Arrange
    user_id = "user-123"

    # Act
    await user_service.update_cloud_messaging_token(user_id=user_id, token="new-device-token-123")

    # Assert
    mock_user_repo.update.assert_called_once_with(user_id, {"cloud_messaging_token": "new-device-token-123"})


# ==========================================
# Update Cloud Messaging Token API Tests
# ==========================================

def test_api_update_cloud_messaging_token_success(client, user_service):
    # Arrange
    user_id = "provider-123"
    token = security.create_access_token({"id": user_id})
    mock_user = User(id=user_id, email="provider@example.com", type=UserType.PROVIDER, is_active=True)
    user_service.get_user = AsyncMock(return_value=mock_user)
    user_service.update_cloud_messaging_token = AsyncMock()

    # Act
    response = client.put(
        "/api/v1/users/cloud-messaging-token",
        json={"token": "firebase-push-token-123"},
        headers={"Authorization": f"Bearer {token}"}
    )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["detail"] == "Cloud messaging token updated successfully."
    user_service.update_cloud_messaging_token.assert_called_once_with(user_id, "firebase-push-token-123")
