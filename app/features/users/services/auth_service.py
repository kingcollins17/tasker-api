"""Authentication and identity sub-service for user registration, login, and OTP verification."""

from typing import Optional
from fastapi import HTTPException, status

from app.core.logging import log_error
from app.core.models.users import CustomerProfile, KYCStatus, ProviderProfile, User, UserType
from app.core.repository import QueryOptions, Repository
from app.core.services import (
    OTPError,
    OTPMaxAttemptsReachedError,
    OTPRateLimitError,
    OTPService,
    OTPVerificationError,
)
from app.core.utils import security
from app.core.utils.datetime_helper import lagos_now
from app.core.utils.phone_helper import format_nigerian_phone
from app.features.users.schemas import UserLogin, UserRegister


class UserAuthService:
    """Sub-service managing core identity lifecycle, registration, login authentication,
    and OTP delivery/verification workflows.
    """

    def __init__(
        self,
        user_repo: Repository[User],
        customer_repo: Repository[CustomerProfile],
        provider_repo: Repository[ProviderProfile],
        otp_service: OTPService,
    ):
        self.user_repo = user_repo
        self.customer_repo = customer_repo
        self.provider_repo = provider_repo
        self.otp_service = otp_service

    @log_error()
    async def register_user(self, schema: UserRegister) -> User:
        """Register a new user (Customer or Provider) and initialize their profile."""
        # 1. Check email uniqueness
        existing_email = await self.user_repo.get_all(
            QueryOptions(filters={"email": schema.email})
        )
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A user with this email already exists.",
            )

        # 2. Check phone number uniqueness if provided
        if schema.phone_number:
            existing_phone = await self.user_repo.get_all(
                QueryOptions(filters={"phone_number": schema.phone_number})
            )
            if existing_phone:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A user with this phone number already exists.",
                )

        # 3. Hash password and persist base user account
        hashed_password = security.hash_password(schema.password)
        user = User(
            email=schema.email,
            phone_number=schema.phone_number,
            hashed_password=hashed_password,
            type=schema.type,
            is_active=True,
            region_id=schema.region_id,
        )
        user = await self.user_repo.add(user)

        # 4. Initialize specific profile based on role (Customer vs Provider)
        if schema.type == UserType.CUSTOMER:
            customer_profile = CustomerProfile(
                user_id=user.id,
                first_name=schema.first_name,
                last_name=schema.last_name,
            )
            await self.customer_repo.add(customer_profile)
        elif schema.type == UserType.PROVIDER:
            provider_profile = ProviderProfile(
                user_id=user.id,
                first_name=schema.first_name,
                last_name=schema.last_name,
                gender=schema.gender,
                status=KYCStatus.PENDING_SUBMISSION,
            )
            await self.provider_repo.add(provider_profile)

        # Refresh user instance to populate relationships
        await self.user_repo.refresh(user)
        return user

    @log_error()
    async def login_user(self, schema: UserLogin) -> dict:
        """Verify credentials, enforce account activity, and return JWT token payload."""
        users = await self.user_repo.get_all(
            QueryOptions(filters={"email": schema.email})
        )
        if not users:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password.",
            )
        user = users[0]

        if not security.verify_password(schema.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password.",
            )

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is inactive.",
            )

        token_payload = {
            "id": user.id,
            "email": user.email,
            "is_active": user.is_active,
        }
        access_token = security.create_access_token(data=token_payload)

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "user": user,
        }

    @log_error()
    async def get_user(self, user_id: str) -> Optional[User]:
        """Fetch user record by unique identifier."""
        return await self.user_repo.get(user_id)

    @log_error()
    async def request_email_otp(self, email: str) -> None:
        """Generate and dispatch verification OTP code to the specified email address."""
        users = await self.user_repo.get_all(QueryOptions(filters={"email": email}))
        if not users:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User with this email does not exist.",
            )

        try:
            await self.otp_service.generate_and_send_otp(target=email, channel="email")
        except OTPRateLimitError as e:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e)
            )
        except OTPError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
            )

    @log_error()
    async def verify_email_otp(self, email: str, code: str) -> User:
        """Verify email OTP code and mark the user's email verification status as true."""
        users = await self.user_repo.get_all(QueryOptions(filters={"email": email}))
        if not users:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User with this email does not exist.",
            )
        user = users[0]

        try:
            verified = await self.otp_service.verify_otp(
                target=email, channel="email", code=code
            )
            if not verified:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid verification code.",
                )
        except (OTPMaxAttemptsReachedError, OTPVerificationError) as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        except OTPError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
            )

        updated_user = await self.user_repo.update(user.id, {"email_verified": True})
        if not updated_user:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update user verification status.",
            )
        return updated_user

    @log_error()
    async def request_phone_otp(self, phone_number: str) -> None:
        """Generate and dispatch verification OTP code via SMS to the specified phone number."""
        phone_number = format_nigerian_phone(phone_number)
        users = await self.user_repo.get_all(
            QueryOptions(filters={"phone_number": phone_number})
        )
        if not users:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User with this phone number does not exist.",
            )

        try:
            await self.otp_service.generate_and_send_otp(
                target=phone_number, channel="sms"
            )
        except OTPRateLimitError as e:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e)
            )
        except OTPError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
            )

    @log_error()
    async def verify_phone_otp(self, phone_number: str, code: str) -> User:
        """Verify SMS OTP code and mark the user's phone verification status as true."""
        phone_number = format_nigerian_phone(phone_number)
        users = await self.user_repo.get_all(
            QueryOptions(filters={"phone_number": phone_number})
        )
        if not users:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User with this phone number does not exist.",
            )
        user = users[0]

        try:
            verified = await self.otp_service.verify_otp(
                target=phone_number, channel="sms", code=code
            )
            if not verified:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid verification code.",
                )
        except (OTPMaxAttemptsReachedError, OTPVerificationError) as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        except OTPError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
            )

        updated_user = await self.user_repo.update(user.id, {"phone_verified": True})
        if not updated_user:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update user verification status.",
            )
        return updated_user

    @log_error()
    async def verify_otp(self, target: str, channel: str, code: str) -> bool:
        """Verify an OTP code for a target and channel without mutating database state."""
        channel = channel.lower()
        if channel == "sms":
            target = format_nigerian_phone(target)

        try:
            verified = await self.otp_service.verify_otp(
                target=target, channel=channel, code=code
            )
            if not verified:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid verification code.",
                )
            return verified
        except (OTPMaxAttemptsReachedError, OTPVerificationError) as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        except OTPError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
            )
