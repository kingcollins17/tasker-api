from typing import Optional
from fastapi import Depends, HTTPException, status
from sqlmodel import select
from app.core.repository import GetRepository, Repository, QueryOptions
from app.core.models.users import User, CustomerProfile, ProviderProfile, UserType, KYCStatus
from app.core.models.services import Service, ProviderServiceLink
from app.core.models.regions import Region
from app.core.utils import security
from app.core.utils.phone_helper import format_nigerian_phone
from app.core.logging import log_error
from app.features.users.schemas import UserRegister, UserLogin
from app.core.utils.datetime_helper import utc_now

        
from app.core.services import (
    OTPService,
    get_otp_service,
    OTPError,
    OTPRateLimitError,
    OTPVerificationError,
    OTPMaxAttemptsReachedError,
)


class UserService:
    def __init__(
        self,
        user_repo: Repository[User],
        customer_repo: Repository[CustomerProfile],
        provider_repo: Repository[ProviderProfile],
        otp_service: OTPService,
        region_repo: Optional[Repository[Region]] = None,
    ):
        self.user_repo = user_repo
        self.customer_repo = customer_repo
        self.provider_repo = provider_repo
        self.otp_service = otp_service
        self.region_repo = region_repo

    @log_error()
    async def register_user(self, schema: UserRegister) -> User:
        # Check email uniqueness
        existing_email = await self.user_repo.get_all(
            QueryOptions(filters={"email": schema.email})
        )
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A user with this email already exists."
            )

        # Check phone number uniqueness if provided
        if schema.phone_number:
            existing_phone = await self.user_repo.get_all(
                QueryOptions(filters={"phone_number": schema.phone_number})
            )
            if existing_phone:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A user with this phone number already exists."
                )

        # Validate region if provided
        if schema.region_id:
            if self.region_repo:
                region = await self.region_repo.get(schema.region_id)
            else:
                region_stmt = select(Region).where(Region.id == schema.region_id)
                region_result = await self.user_repo.execute(region_stmt)
                region = region_result.first()
            if not region:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="The specified region does not exist."
                )

        # Hash the user's password
        hashed_password = security.hash_password(schema.password)

        # Create user
        user = User(
            email=schema.email,
            phone_number=schema.phone_number,
            hashed_password=hashed_password,
            type=schema.type,
            is_active=True,  # Set active on registration by default
            region_id=schema.region_id,
        )
        user = await self.user_repo.add(user)

        # Create profile based on user type
        if schema.type == UserType.CUSTOMER:
            customer_profile = CustomerProfile(
                user_id=user.id,
                first_name=schema.first_name,
                last_name=schema.last_name
            )
            await self.customer_repo.add(customer_profile)
        elif schema.type == UserType.PROVIDER:
            provider_profile = ProviderProfile(
                user_id=user.id,
                first_name=schema.first_name,
                last_name=schema.last_name,
                gender=schema.gender,
                status=KYCStatus.PENDING_SUBMISSION
            )
            await self.provider_repo.add(provider_profile)

        # Refresh to populate relationships
        await self.user_repo.session.refresh(user)
        return user

    @log_error()
    async def request_email_otp(self, email: str) -> None:
        """Generates and sends an OTP to the user's email."""
        # Verify user exists
        users = await self.user_repo.get_all(QueryOptions(filters={"email": email}))
        if not users:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User with this email does not exist."
            )

        try:
            await self.otp_service.generate_and_send_otp(target=email, channel="email")
        except OTPRateLimitError as e:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(e)
            )
        except OTPError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e)
            )

    @log_error()
    async def verify_email_otp(self, email: str, code: str) -> User:
        """Verifies the OTP and marks the user's email as verified."""
        users = await self.user_repo.get_all(QueryOptions(filters={"email": email}))
        if not users:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User with this email does not exist."
            )
        user = users[0]

        try:
            verified = await self.otp_service.verify_otp(target=email, channel="email", code=code)
            if not verified:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid verification code."
                )
        except OTPMaxAttemptsReachedError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        except OTPVerificationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        except OTPError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e)
            )

        # Update user record in database
        updated_user = await self.user_repo.update(user.id, {"email_verified": True})
        if not updated_user:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update user verification status."
            )
        return updated_user

    @log_error()
    async def request_phone_otp(self, phone_number: str) -> None:
        """Generates and sends an OTP to the user's phone number."""
        phone_number = format_nigerian_phone(phone_number)
        # Verify user exists
        users = await self.user_repo.get_all(QueryOptions(filters={"phone_number": phone_number}))
        if not users:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User with this phone number does not exist."
            )

        try:
            await self.otp_service.generate_and_send_otp(target=phone_number, channel="sms")
        except OTPRateLimitError as e:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(e)
            )
        except OTPError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e)
            )

    @log_error()
    async def verify_phone_otp(self, phone_number: str, code: str) -> User:
        """Verifies the OTP and marks the user's phone number as verified."""
        phone_number = format_nigerian_phone(phone_number)
        # Verify user exists
        users = await self.user_repo.get_all(QueryOptions(filters={"phone_number": phone_number}))
        if not users:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User with this phone number does not exist."
            )
        user = users[0]

        try:
            verified = await self.otp_service.verify_otp(target=phone_number, channel="sms", code=code)
            if not verified:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid verification code."
                )
        except OTPMaxAttemptsReachedError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        except OTPVerificationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        except OTPError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e)
            )

        # Update user record in database
        updated_user = await self.user_repo.update(user.id, {"phone_verified": True})
        if not updated_user:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update user verification status."
            )
        return updated_user

    @log_error()
    async def login_user(self, schema: UserLogin) -> dict:
        """Verifies credentials, ensures user is active, and returns JWT along with user details."""
        users = await self.user_repo.get_all(QueryOptions(filters={"email": schema.email}))
        if not users:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password."
            )
        user = users[0]

        if not security.verify_password(schema.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password."
            )

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is inactive."
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
    async def get_user(self, user_id: str) -> User | None:
        """Fetch a user record by its ID."""
        return await self.user_repo.get(user_id)

    @log_error()
    async def submit_kyc(
        self,
        user_id: str,
        id_type: str,
        id_number: str,
        id_doc_url: str,
        selfie_url: str
    ) -> ProviderProfile:
        """Submit KYC details for a provider and transition status to SUBMITTED."""
        
        # Check if provider profile exists
        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found."
            )
        profile = profiles[0]

        # Update provider profile fields
        updated_profile = await self.provider_repo.update(
            profile.id,
            {
                "id_type": id_type,
                "id_number": id_number,
                "id_doc_url": id_doc_url,
                "selfie_url": selfie_url,
                "status": KYCStatus.SUBMITTED,
                "updated_at": utc_now()
            }
        )
        if not updated_profile:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update provider profile."
            )
        return updated_profile

    @log_error()
    async def update_provider_profile(
        self,
        user_id: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        gender: Optional[str] = None,
        phone_number: Optional[str] = None,
    ) -> User:
        """Update provider profile details (first_name, last_name, gender) and user details (phone_number)."""

        # 1. Update phone number on the User model if requested
        if phone_number is not None:
            phone_number = format_nigerian_phone(phone_number)
            # Check uniqueness
            existing = await self.user_repo.get_all(
                QueryOptions(filters={"phone_number": phone_number})
            )
            if existing and existing[0].id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A user with this phone number already exists."
                )
            
            await self.user_repo.update(user_id, {"phone_number": phone_number})

        # 2. Update provider profile details
        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found."
            )
        profile = profiles[0]

        profile_updates = {}
        if first_name is not None:
            profile_updates["first_name"] = first_name
        if last_name is not None:
            profile_updates["last_name"] = last_name
        if gender is not None:
            profile_updates["gender"] = gender

        if profile_updates:
            profile_updates["updated_at"] = utc_now()
            await self.provider_repo.update(profile.id, profile_updates)

        # 3. Retrieve and return the updated user with relationships
        user = await self.user_repo.get(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found."
            )
        await self.user_repo.session.refresh(user)
        return user

    @log_error()
    async def verify_otp(self, target: str, channel: str, code: str) -> bool:
        """Verifies the OTP code for a target and channel without writing/updating DB."""
        channel = channel.lower()
        if channel == "sms":
            from app.core.utils.phone_helper import format_nigerian_phone
            target = format_nigerian_phone(target)

        try:
            verified = await self.otp_service.verify_otp(target=target, channel=channel, code=code)
            if not verified:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid verification code."
                )
            return verified
        except OTPMaxAttemptsReachedError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        except OTPVerificationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        except OTPError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e)
            )

    @log_error()
    async def update_customer_profile(
        self,
        user_id: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> User:
        """Update customer (seeker) profile details (first_name, last_name)."""
        
        profiles = await self.customer_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Customer profile not found."
            )
        profile = profiles[0]

        profile_updates = {}
        if first_name is not None:
            profile_updates["first_name"] = first_name
        if last_name is not None:
            profile_updates["last_name"] = last_name

        if profile_updates:
            profile_updates["updated_at"] = utc_now()
            await self.customer_repo.update(profile.id, profile_updates)

        user = await self.user_repo.get(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found."
            )
        await self.user_repo.session.refresh(user)
        return user

    @log_error()
    async def update_user_location(
        self,
        user_id: str,
        user_type: UserType,
        latitude: float,
        longitude: float,
        address_line: Optional[str] = None
    ) -> None:
        """Update last known location for customer or provider profile."""
        # from app.core.utils.datetime_helper import utc_now
        
        # Represent the POINT in Well-Known Text (WKT) format
        wkt_point = f"POINT({longitude} {latitude})"
 
        if user_type == UserType.CUSTOMER:
            profiles = await self.customer_repo.get_all(
                QueryOptions(filters={"user_id": user_id})
            )
            if not profiles:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Customer profile not found."
                )
            profile = profiles[0]
            await self.customer_repo.update(
                profile.id,
                {"last_known_location": wkt_point, "address_line": address_line, "updated_at": utc_now()}
            )
        elif user_type == UserType.PROVIDER:
            profiles = await self.provider_repo.get_all(
                QueryOptions(filters={"user_id": user_id})
            )
            if not profiles:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Provider profile not found."
                )
            profile = profiles[0]
            await self.provider_repo.update(
                profile.id,
                {"last_known_location": wkt_point, "address_line": address_line, "updated_at": utc_now()}
            )

    @log_error()
    async def update_cloud_messaging_token(self, user_id: str, token: str) -> None:
        """Update cloud messaging token for a user."""
        await self.user_repo.update(user_id, {"cloud_messaging_token": token})

    @log_error()
    async def attach_provider_service(self, user_id: str, service_id: str) -> None:
        """Associate a service with the provider, enforcing a maximum of 3 active services."""
        # 1. Fetch provider profile
        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found."
            )

        # 2. Get the Service instance to verify existence and active status
        service_stmt = select(Service).where(Service.id == service_id)
        service_result = await self.provider_repo.execute(service_stmt)
        service = service_result.first()
        if not service:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Service not found."
            )
        if not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot add an inactive service."
            )

        # 3. Fetch existing links to check limits and duplicate associations
        link_stmt = select(ProviderServiceLink).where(ProviderServiceLink.provider_id == user_id)
        link_result = await self.provider_repo.execute(link_stmt)
        existing_links = list(link_result.all())

        is_already_added = any(link.service_id == service_id for link in existing_links)
        if is_already_added:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Service is already added to this provider."
            )
        if len(existing_links) >= 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A provider can have a maximum of 3 services."
            )
        
        new_link = ProviderServiceLink(provider_id=user_id, service_id=service_id)
        self.provider_repo.session.add(new_link)
        await self.provider_repo.session.commit()

    @log_error()
    async def remove_provider_service(self, user_id: str, service_id: str) -> None:
        """Remove a service association from the provider."""
        # 1. Fetch provider profile
        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found."
            )

        # 2. Get the Service instance
        service_stmt = select(Service).where(Service.id == service_id)
        service_result = await self.provider_repo.execute(service_stmt)
        service = service_result.first()
        if not service:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Service not found."
            )

        # 3. Fetch existing links to locate and remove association
        link_stmt = select(ProviderServiceLink).where(ProviderServiceLink.provider_id == user_id)
        link_result = await self.provider_repo.execute(link_stmt)
        existing_links = list(link_result.all())

        is_already_added = any(link.service_id == service_id for link in existing_links)
        if not is_already_added:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Service is not associated with this provider."
            )
        
        target_link = next(link for link in existing_links if link.service_id == service_id)
        await self.provider_repo.session.delete(target_link)
        await self.provider_repo.session.commit()






def get_user_service(
    user_repo: Repository[User] = Depends(GetRepository(User)),
    customer_repo: Repository[CustomerProfile] = Depends(GetRepository(CustomerProfile)),
    provider_repo: Repository[ProviderProfile] = Depends(GetRepository(ProviderProfile)),
    otp_service: OTPService = Depends(get_otp_service),
    region_repo: Repository[Region] = Depends(GetRepository(Region)),
) -> UserService:
    return UserService(
        user_repo=user_repo,
        customer_repo=customer_repo,
        provider_repo=provider_repo,
        otp_service=otp_service,
        region_repo=region_repo,
    )
