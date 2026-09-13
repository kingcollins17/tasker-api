from typing import List, Optional
from datetime import datetime
from fastapi import Depends, HTTPException, status

from app.core.logging import log_error
from app.core.models.users import (
    KYCStatus,
    OnboardingStep,
    ProviderProfile,
    User,
    UserStats,
)
from app.core.models.vetting import InterviewStatus, ProviderInterview
from app.core.repository import GetRepository, QueryOptions, Repository
from app.core.utils.datetime_helper import lagos_now


class InterviewManagerService:
    """Service class for scheduling, managing, and reviewing provider online interviews."""

    def __init__(
        self,
        interview_repo: Repository[ProviderInterview],
        user_repo: Repository[User],
        provider_repo: Repository[ProviderProfile],
        stats_repo: Repository[UserStats],
    ):
        self.interview_repo = interview_repo
        self.user_repo = user_repo
        self.provider_repo = provider_repo
        self.stats_repo = stats_repo

    @log_error()
    async def schedule_interview(
        self,
        user_id: str,
        scheduled_at: datetime,
        meeting_link: Optional[str] = None,
        notes: Optional[str] = None,
        admin_id: Optional[str] = None,
    ) -> ProviderInterview:
        """Schedule an online interview for a user who has completed KYC verification."""
        user = await self.user_repo.get(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found.",
            )

        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found for user.",
            )

        profile = profiles[0]
        if profile.kyc_status != KYCStatus.VERIFIED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User must complete KYC verification before scheduling an interview.",
            )

        now = lagos_now()
        interview_data = ProviderInterview(
            user_id=user_id,
            admin_id=admin_id,
            scheduled_at=scheduled_at,
            meeting_link=meeting_link,
            status=InterviewStatus.SCHEDULED,
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        created_interview = await self.interview_repo.add(interview_data)

        await self.provider_repo.update(
            profile.id,
            {
                "current_onboarding_step": OnboardingStep.INTERVIEW,
                "updated_at": now,
            },
        )

        return created_interview

    @log_error()
    async def update_interview_status(
        self,
        interview_id: str,
        interview_status: InterviewStatus,
        notes: Optional[str] = None,
        meeting_link: Optional[str] = None,
        scheduled_at: Optional[datetime] = None,
        admin_id: Optional[str] = None,
    ) -> ProviderInterview:
        """Update interview status, marking whether user passed or failed, and incrementing current_tier on UserStats if passed."""
        interview = await self.interview_repo.get(interview_id)
        if not interview:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Interview record not found.",
            )

        now = lagos_now()
        updates: dict = {
            "status": interview_status,
            "updated_at": now,
        }
        if notes is not None:
            updates["notes"] = notes
        if meeting_link is not None:
            updates["meeting_link"] = meeting_link
        if scheduled_at is not None:
            updates["scheduled_at"] = scheduled_at
        if admin_id is not None:
            updates["admin_id"] = admin_id

        if interview_status == InterviewStatus.PASSED:
            updates["passed_at"] = now

        updated_interview = await self.interview_repo.update(interview.id, updates)
        target_interview = updated_interview or interview

        if interview_status == InterviewStatus.PASSED:
            # Upgrade provider tier in UserStats to tier 4
            stats_list = await self.stats_repo.get_all(
                QueryOptions(filters={"user_id": target_interview.user_id})
            )
            if stats_list:
                user_stat = stats_list[0]
                await self.stats_repo.update(
                    user_stat.id,
                    {
                        "current_tier": max(user_stat.current_tier, 4),
                        "updated_at": now,
                    },
                )
            else:
                await self.stats_repo.add(
                    UserStats(
                        user_id=target_interview.user_id,
                        current_tier=4,
                        created_at=now,
                        updated_at=now,
                    )
                )

            # Update provider onboarding step to COMPLETED
            profiles = await self.provider_repo.get_all(
                QueryOptions(filters={"user_id": target_interview.user_id})
            )
            if profiles:
                await self.provider_repo.update(
                    profiles[0].id,
                    {
                        "current_onboarding_step": OnboardingStep.COMPLETED,
                        "updated_at": now,
                    },
                )

        return target_interview

    @log_error()
    async def get_user_interview(self, user_id: str) -> Optional[ProviderInterview]:
        """Fetch the most recent interview record for a provider."""
        interviews = await self.interview_repo.get_all(
            QueryOptions(
                filters={"user_id": user_id},
                order_by="created_at",
                descending=True,
                limit=1,
            )
        )
        return interviews[0] if interviews else None

    @log_error()
    async def list_interviews(
        self,
        interview_status: Optional[InterviewStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ProviderInterview]:
        """List provider interviews for admin management."""
        filters = {}
        if interview_status:
            filters["status"] = interview_status
        return await self.interview_repo.get_all(
            QueryOptions(
                filters=filters,
                order_by="scheduled_at",
                descending=True,
                limit=limit,
                offset=offset,
            )
        )


def get_interview_manager_service(
    interview_repo: Repository[ProviderInterview] = Depends(GetRepository(ProviderInterview)),
    user_repo: Repository[User] = Depends(GetRepository(User)),
    provider_repo: Repository[ProviderProfile] = Depends(GetRepository(ProviderProfile)),
    stats_repo: Repository[UserStats] = Depends(GetRepository(UserStats)),
) -> InterviewManagerService:
    """Dependency provider injecting repositories into InterviewManagerService."""
    return InterviewManagerService(
        interview_repo=interview_repo,
        user_repo=user_repo,
        provider_repo=provider_repo,
        stats_repo=stats_repo,
    )
