"""Customer (seeker) profile sub-service for profile management."""

from typing import Optional
from fastapi import HTTPException, status

from app.core.logging import log_error
from app.core.models.users import CustomerProfile, User
from app.core.repository import QueryOptions, Repository
from app.core.utils.datetime_helper import lagos_now
from app.core.utils.phone_helper import format_nigerian_phone


class CustomerProfileService:
    """Sub-service managing customer (seeker) profile updates and account details."""

    def __init__(
        self,
        user_repo: Repository[User],
        customer_repo: Repository[CustomerProfile],
    ):
        self.user_repo = user_repo
        self.customer_repo = customer_repo

    @log_error()
    async def update_customer_profile(
        self,
        user_id: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        phone_number: Optional[str] = None,
    ) -> User:
        """Update customer (seeker) profile details (first_name, last_name) and user details (phone_number)."""
        # 1. Update phone number on the core User model if provided
        if phone_number is not None:
            phone_number = format_nigerian_phone(phone_number)

            user = await self.user_repo.get(user_id)
            if user and user.phone_number != phone_number:
                # Check uniqueness across existing accounts
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

        # 2. Update customer profile attributes
        profiles = await self.customer_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Customer profile not found.",
            )
        profile = profiles[0]

        profile_updates = {}
        if first_name is not None:
            profile_updates["first_name"] = first_name
        if last_name is not None:
            profile_updates["last_name"] = last_name

        if profile_updates:
            profile_updates["updated_at"] = lagos_now()
            await self.customer_repo.update(profile.id, profile_updates)

        # 3. Retrieve and return updated User instance with relationships
        user = await self.user_repo.get(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
            )
        await self.user_repo.refresh(user)
        return user
