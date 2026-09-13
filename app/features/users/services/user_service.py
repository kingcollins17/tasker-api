"""UserService focusing on core user entity retrieval."""

from typing import Optional
from fastapi import Depends

from app.core.logging import log_error
from app.core.models.users import User
from app.core.repository import GetRepository, QueryOptions, Repository


class UserService:
    """Service focused on user fetching and core user account lookup operations."""

    def __init__(self, user_repo: Repository[User]):
        self.user_repo = user_repo

    @log_error()
    async def get_user(self, user_id: str) -> Optional[User]:
        """Fetch user by unique primary key ID."""
        return await self.user_repo.get(user_id)

    @log_error()
    async def get_by_email(self, email: str) -> Optional[User]:
        """Fetch user by unique email address."""
        users = await self.user_repo.get_all(
            QueryOptions(filters={"email": email})
        )
        return users[0] if users else None


def get_user_service(
    user_repo: Repository[User] = Depends(GetRepository(User)),
) -> UserService:
    """Dependency provider injecting Repository[User] into UserService."""
    return UserService(user_repo=user_repo)
