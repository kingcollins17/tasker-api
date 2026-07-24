"""Provider (tasker) profile sub-service for profile management, KYC, service attachment, and presence."""

from typing import Optional
from fastapi import HTTPException, status
from sqlmodel import select

from app.core.logging import log_error
from app.core.models.services import ProviderServiceLink, Service
from app.core.models.users import DutyStatus, KYCStatus, ProviderProfile, User
from app.core.repository import QueryOptions, Repository
from app.core.services.provider_location import ProviderLocationService
from app.core.utils.datetime_helper import utc_now
from app.core.utils.phone_helper import format_nigerian_phone


class ProviderProfileService:
    """Sub-service managing provider profile details, KYC verification workflow,

    service catalog link attachments (max 3), and online presence state.
    """

    def __init__(
        self,
        user_repo: Repository[User],
        provider_repo: Repository[ProviderProfile],
        provider_location_service: Optional[ProviderLocationService] = None,
    ):
        self.user_repo = user_repo
        self.provider_repo = provider_repo
        self.provider_location_service = provider_location_service

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
        # 1. Update phone number on core User model if requested
        if phone_number is not None:
            phone_number = format_nigerian_phone(phone_number)

            user = await self.user_repo.get(user_id)
            if user and user.phone_number != phone_number:
                existing = await self.user_repo.get_all(
                    QueryOptions(filters={"phone_number": phone_number})
                )
                if existing and existing[0].id != user_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="A user with this phone number already exists.",
                    )

                await self.user_repo.update(
                    user_id, {"phone_number": phone_number, "phone_verified": False}
                )

        # 2. Update provider profile details
        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found.",
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

        # 3. Retrieve and return updated User instance with relationships
        user = await self.user_repo.get(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
            )
        await self.user_repo.refresh(user)
        return user

    @log_error()
    async def submit_kyc(
        self,
        user_id: str,
        id_type: str,
        id_number: str,
        id_doc_url: str,
        selfie_url: str,
    ) -> ProviderProfile:
        """Submit KYC details for a provider and transition status to SUBMITTED."""
        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found.",
            )
        profile = profiles[0]

        updated_profile = await self.provider_repo.update(
            profile.id,
            {
                "id_type": id_type,
                "id_number": id_number,
                "id_doc_url": id_doc_url,
                "selfie_url": selfie_url,
                "status": KYCStatus.SUBMITTED,
                "updated_at": utc_now(),
            },
        )
        if not updated_profile:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update provider profile.",
            )
        return updated_profile

    @log_error()
    async def submit_kyc_selfie(self, user_id: str, selfie_url: str) -> ProviderProfile:
        """Submit KYC verification selfie."""
        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found.",
            )
        profile = profiles[0]

        updated_profile = await self.provider_repo.update(
            profile.id, {"selfie_url": selfie_url, "updated_at": utc_now()}
        )
        if not updated_profile:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update provider profile.",
            )
        return updated_profile

    @log_error()
    async def submit_kyc_document(
        self, user_id: str, id_type: str, id_number: str, id_doc_url: str
    ) -> ProviderProfile:
        """Submit KYC document details and transition status to SUBMITTED if selfie is present."""
        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found.",
            )
        profile = profiles[0]

        new_status = profile.status
        if profile.selfie_url:
            new_status = KYCStatus.SUBMITTED

        updated_profile = await self.provider_repo.update(
            profile.id,
            {
                "id_type": id_type,
                "id_number": id_number,
                "id_doc_url": id_doc_url,
                "status": new_status,
                "updated_at": utc_now(),
            },
        )
        if not updated_profile:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update provider profile.",
            )
        return updated_profile

    @log_error()
    async def attach_provider_service(self, user_id: str, service_id: str) -> None:
        """Associate a service with the provider, enforcing a maximum limit of 3 active services."""
        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found.",
            )

        service_stmt = select(Service).where(Service.id == service_id)
        service_result = await self.provider_repo.execute(service_stmt)
        service = service_result.first()
        if not service:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Service not found."
            )
        if not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot add an inactive service.",
            )

        link_stmt = select(ProviderServiceLink).where(
            ProviderServiceLink.provider_id == user_id
        )
        link_result = await self.provider_repo.execute(link_stmt)
        existing_links = list(link_result.all())

        is_already_added = any(link.service_id == service_id for link in existing_links)
        if is_already_added:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Service is already added to this provider.",
            )
        if len(existing_links) >= 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A provider can have a maximum of 3 services.",
            )

        new_link = ProviderServiceLink(provider_id=user_id, service_id=service_id)
        self.provider_repo.session.add(new_link)
        await self.provider_repo.session.commit()

    @log_error()
    async def remove_provider_service(self, user_id: str, service_id: str) -> None:
        """Remove a service association from the provider."""
        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found.",
            )

        service_stmt = select(Service).where(Service.id == service_id)
        service_result = await self.provider_repo.execute(service_stmt)
        service = service_result.first()
        if not service:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Service not found."
            )

        link_stmt = select(ProviderServiceLink).where(
            ProviderServiceLink.provider_id == user_id
        )
        link_result = await self.provider_repo.execute(link_stmt)
        existing_links = list(link_result.all())

        is_already_added = any(link.service_id == service_id for link in existing_links)
        if not is_already_added:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Service is not associated with this provider.",
            )

        target_link = next(
            link for link in existing_links if link.service_id == service_id
        )
        await self.provider_repo.session.delete(target_link)
        await self.provider_repo.session.commit()

    @log_error()
    async def update_provider_online_status(self, user_id: str, is_online: bool) -> User:
        """Update provider online presence status and synchronize with Redis spatial index."""
        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found.",
            )
        profile = profiles[0]

        new_duty_status = profile.duty_status
        if profile.duty_status not in (DutyStatus.ON_TASK, DutyStatus.ON_DISPATCH):
            new_duty_status = DutyStatus.ONLINE_AVAILABLE if is_online else DutyStatus.OFFLINE

        await self.provider_repo.update(
            profile.id,
            {
                "is_online": is_online,
                "duty_status": new_duty_status,
                "updated_at": utc_now(),
            },
        )

        if not is_online and self.provider_location_service:
            await self.provider_location_service.remove_provider_location(user_id)

        user = await self.user_repo.get(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
            )
        await self.user_repo.refresh(user)
        return user
