"""VettingService managing provider guarantor submissions, resubmissions, approvals, and rejections."""

import secrets
from typing import List, Optional
from fastapi import Depends, HTTPException, status

from app.core.logging import log_error
from app.core.models.users import (
    KYCDocument,
    KYCStatus,
    OnboardingStep,
    ProviderProfile,
    UserStats,
    VerificationStatus,
)
from app.core.models.vetting import ProviderGuarantor
from app.core.policies.guarantor_policy import GuarantorPolicy
from app.core.repository import GetRepository, QueryOptions, Repository
from app.core.utils.datetime_helper import lagos_now
from app.features.vetting.schemas import (
    AddGuarantorRequest,
    ResubmitGuarantorRequest,
)


class VettingService:
    """Service handling guarantor verification lifecycle including submission, resubmission, approval, and rejection."""

    def __init__(
        self,
        guarantor_repo: Repository[ProviderGuarantor],
        provider_repo: Optional[Repository[ProviderProfile]] = None,
        kyc_repo: Optional[Repository[KYCDocument]] = None,
        stats_repo: Optional[Repository[UserStats]] = None,
    ):
        self.guarantor_repo = guarantor_repo
        self.provider_repo = provider_repo
        self.kyc_repo = kyc_repo
        self.stats_repo = stats_repo

    @log_error()
    async def get_guarantor_history(self, provider_id: str) -> List[ProviderGuarantor]:
        """Fetch all guarantor records for a provider sorted by creation date descending."""
        return await self.guarantor_repo.get_all(
            QueryOptions(
                filters={"provider_id": provider_id},
                order_by="created_at",
                descending=True,
            )
        )

    @log_error()
    async def get_latest_guarantor(
        self, provider_id: str
    ) -> Optional[ProviderGuarantor]:
        """Fetch the most recent guarantor submission for a provider."""
        guarantors = await self.get_guarantor_history(provider_id)
        return guarantors[0] if guarantors else None

    @log_error()
    async def add_guarantor(
        self, provider_id: str, guarantor_data: AddGuarantorRequest
    ) -> ProviderGuarantor:
        """Submit an initial guarantor attestation for a provider."""
        guarantors = await self.get_guarantor_history(provider_id)

        if any(g.status == VerificationStatus.PASSED for g in guarantors):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Guarantor verification has already passed for this provider.",
            )

        if any(g.status in GuarantorPolicy.PENDING_STATUSES for g in guarantors):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A guarantor submission is currently pending review. You cannot submit until review is complete.",
            )

        attempt_count = len(guarantors)
        if GuarantorPolicy.is_max_attempts_reached(attempt_count):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum guarantor submission attempts limit ({GuarantorPolicy.MAX_ATTEMPTS}) has been reached.",
            )

        token_hash = secrets.token_urlsafe(32)
        meta_data = {
            "attempt_number": attempt_count + 1,
            "submitted_at": lagos_now().isoformat(),
        }

        guarantor = ProviderGuarantor(
            provider_id=provider_id,
            guarantor_name=guarantor_data.guarantor_name,
            guarantor_phone=guarantor_data.guarantor_phone,
            relationship=guarantor_data.relationship,
            token_hash=token_hash,
            status=VerificationStatus.PENDING,
            meta_data=meta_data,
        )

        return await self.guarantor_repo.add(guarantor)

    @log_error()
    async def resubmit_guarantor(
        self, provider_id: str, guarantor_data: ResubmitGuarantorRequest
    ) -> ProviderGuarantor:
        """Resubmit a new guarantor entry if all previous submissions failed and attempt limit is not exceeded."""
        guarantors = await self.get_guarantor_history(provider_id)

        if not guarantors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No previous guarantor submission found. Use add_guarantor for initial submission.",
            )

        if any(g.status == VerificationStatus.PASSED for g in guarantors):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Guarantor verification has already passed. Resubmission is not allowed.",
            )

        if any(g.status in GuarantorPolicy.PENDING_STATUSES for g in guarantors):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A guarantor submission is currently pending review. Resubmission is not allowed until review is complete.",
            )

        attempt_count = len(guarantors)
        if not GuarantorPolicy.can_resubmit(attempt_count):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum guarantor submission attempts ({GuarantorPolicy.MAX_ATTEMPTS}) reached.",
            )

        token_hash = secrets.token_urlsafe(32)
        meta_data = {
            "attempt_number": attempt_count + 1,
            "resubmitted_at": lagos_now().isoformat(),
        }

        guarantor = ProviderGuarantor(
            provider_id=provider_id,
            guarantor_name=guarantor_data.guarantor_name,
            guarantor_phone=guarantor_data.guarantor_phone,
            relationship=guarantor_data.relationship,
            token_hash=token_hash,
            status=VerificationStatus.PENDING,
            meta_data=meta_data,
        )

        return await self.guarantor_repo.add(guarantor)

    @log_error()
    async def approve_guarantor(
        self, guarantor_id: str, notes: Optional[str] = None
    ) -> Optional[ProviderGuarantor]:
        """Approve a pending guarantor submission and update meta_data with optional notes."""
        guarantor = await self.guarantor_repo.get(guarantor_id)
        if not guarantor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Guarantor entry not found.",
            )

        if guarantor.status == VerificationStatus.PASSED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Guarantor verification has already passed.",
            )

        now = lagos_now()
        meta_data = dict(guarantor.meta_data or {})
        if notes:
            meta_data["notes"] = notes
        meta_data["approved_at"] = now.isoformat()

        updates = {
            "status": VerificationStatus.PASSED,
            "verified_at": now,
            "meta_data": meta_data,
        }
        updated_guarantor = await self.guarantor_repo.update(guarantor_id, updates)

        if self.provider_repo:
            profiles = await self.provider_repo.get_all(
                QueryOptions(filters={"user_id": guarantor.provider_id})
            )
            if profiles:
                profile = profiles[0]
                if profile.kyc_status == KYCStatus.VERIFIED or getattr(profile, "status", None) == KYCStatus.VERIFIED:
                    profile_updates = {
                        "current_onboarding_step": OnboardingStep.INTERVIEW,
                        "updated_at": now,
                    }
                    await self.provider_repo.update(profile.id, profile_updates)

                    if self.stats_repo:
                        stats_list = await self.stats_repo.get_all(
                            QueryOptions(filters={"user_id": guarantor.provider_id})
                        )
                        if stats_list:
                            stat = stats_list[0]
                            await self.stats_repo.update(
                                stat.id,
                                {
                                    "current_tier": max(stat.current_tier, 3),
                                    "updated_at": now,
                                },
                            )
                        else:
                            await self.stats_repo.add(
                                UserStats(
                                    user_id=guarantor.provider_id,
                                    current_tier=3,
                                    created_at=now,
                                    updated_at=now,
                                )
                            )

        return updated_guarantor or guarantor

    @log_error()
    async def reject_guarantor(
        self, guarantor_id: str, reason: str, notes: Optional[str] = None
    ) -> Optional[ProviderGuarantor]:
        """Reject a guarantor submission recording rejection reason and optional notes in meta_data."""
        guarantor = await self.guarantor_repo.get(guarantor_id)
        if not guarantor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Guarantor entry not found.",
            )

        if guarantor.status == VerificationStatus.PASSED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot reject a passed guarantor verification.",
            )

        now = lagos_now()
        meta_data = dict(guarantor.meta_data or {})
        meta_data["rejection_reason"] = reason
        if notes:
            meta_data["notes"] = notes
        meta_data["rejected_at"] = now.isoformat()

        updates = {
            "status": VerificationStatus.FAILED,
            "meta_data": meta_data,
        }
        await self.guarantor_repo.update(guarantor_id, updates)


def get_vetting_service(
    guarantor_repo: Repository[ProviderGuarantor] = Depends(
        GetRepository(ProviderGuarantor)
    ),
    provider_repo: Repository[ProviderProfile] = Depends(
        GetRepository(ProviderProfile)
    ),
    kyc_repo: Repository[KYCDocument] = Depends(
        GetRepository(KYCDocument)
    ),
    stats_repo: Repository[UserStats] = Depends(
        GetRepository(UserStats)
    ),
) -> VettingService:
    """Dependency provider injecting repositories into VettingService."""
    return VettingService(
        guarantor_repo=guarantor_repo,
        provider_repo=provider_repo,
        kyc_repo=kyc_repo,
        stats_repo=stats_repo,
    )
