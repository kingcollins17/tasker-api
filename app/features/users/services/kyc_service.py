"""KYCService managing identity verification submissions, resubmissions, approvals, and rejections."""

from typing import List, Optional
from fastapi import Depends, HTTPException, status

from app.core.logging import log_error
from app.core.models.users import (
    KYCDocument,
    KYCStatus,
    OnboardingStep,
    ProviderProfile,
    User,
    UserStats,
    VerificationStatus,
)
from app.core.models.vetting import ProviderGuarantor
from app.core.policies.kyc_policy import KYCPolicy
from app.core.repository import GetRepository, QueryOptions, Repository
from app.core.utils.datetime_helper import lagos_now


class KYCService:
    """Service managing provider KYC document verification submissions,

    resubmission limits, approval decisions, and rejection tracking.
    """

    def __init__(
        self,
        kyc_repo: Repository[KYCDocument],
        provider_repo: Repository[ProviderProfile],
        user_repo: Repository[User],
        guarantor_repo: Optional[Repository[ProviderGuarantor]] = None,
        stats_repo: Optional[Repository[UserStats]] = None,
    ):
        self.kyc_repo = kyc_repo
        self.provider_repo = provider_repo
        self.user_repo = user_repo
        self.guarantor_repo = guarantor_repo
        self.stats_repo = stats_repo

    @log_error()
    async def get_user_kyc_history(self, user_id: str) -> List[KYCDocument]:
        """Fetch all KYC submission attempts for a given user ordered by attempt number."""
        return await self.kyc_repo.get_all(
            QueryOptions(filters={"user_id": user_id}, order_by="attempt_number", descending=False)
        )

    @log_error()
    async def submit_kyc(
        self,
        user_id: str,
        id_type: str,
        id_number: str,
        id_doc_url: str,
        selfie_url: Optional[str] = None,
    ) -> KYCDocument:
        """Submit initial KYC verification details for a provider."""
        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found.",
            )
        profile = profiles[0]

        existing_docs = await self.get_user_kyc_history(user_id)

        # 1. Check if user already passed review before
        if any(doc.status == KYCStatus.VERIFIED for doc in existing_docs):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="KYC verification has already been completed and passed review.",
            )

        # 2. Check if user has a pending submission
        if any(doc.status in (KYCStatus.SUBMITTED, KYCStatus.UNDER_REVIEW) for doc in existing_docs):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A KYC submission is currently pending review.",
            )

        # 3. Enforce max attempts
        attempt_count = len(existing_docs)
        if KYCPolicy.is_max_attempts_reached(attempt_count):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum KYC submission attempts ({KYCPolicy.MAX_ATTEMPTS}) reached.",
            )

        attempt_number = attempt_count + 1
        new_doc = KYCDocument(
            user_id=user_id,
            provider_profile_id=profile.id,
            id_type=id_type,
            id_number=id_number,
            id_doc_url=id_doc_url,
            status=KYCStatus.SUBMITTED,
            attempt_number=attempt_number,
            submitted_at=lagos_now(),
        )
        doc = await self.kyc_repo.add(new_doc)

        # Sync ProviderProfile state
        profile_updates: dict = {
            "kyc_status": KYCStatus.SUBMITTED,
            "rejection_reason": None,
            "updated_at": lagos_now(),
        }
        if selfie_url:
            profile_updates["selfie_url"] = selfie_url

        await self.provider_repo.update(profile.id, profile_updates)
        return doc

    @log_error()
    async def resubmit_kyc(
        self,
        user_id: str,
        id_type: str,
        id_number: str,
        id_doc_url: str,
        selfie_url: Optional[str] = None,
    ) -> KYCDocument:
        """Resubmit KYC verification document after a previous review failure."""
        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if not profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found.",
            )
        profile = profiles[0]

        existing_docs = await self.get_user_kyc_history(user_id)

        # 1. Ensure no submission has passed review before
        if any(doc.status == KYCStatus.VERIFIED for doc in existing_docs):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="KYC verification has already passed review. Resubmission is not permitted.",
            )

        # 2. Ensure no current submission is pending
        if any(doc.status in (KYCStatus.SUBMITTED, KYCStatus.UNDER_REVIEW) for doc in existing_docs):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A KYC submission is currently pending review.",
            )

        # 3. Ensure previous attempt failed
        if existing_docs and existing_docs[-1].status != KYCStatus.FAILED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Resubmission is only allowed if review for the previous submission failed.",
            )

        # 4. Check max attempts policy
        attempt_count = len(existing_docs)
        if KYCPolicy.is_max_attempts_reached(attempt_count):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum KYC submission attempts ({KYCPolicy.MAX_ATTEMPTS}) reached.",
            )

        attempt_number = attempt_count + 1
        new_doc = KYCDocument(
            user_id=user_id,
            provider_profile_id=profile.id,
            id_type=id_type,
            id_number=id_number,
            id_doc_url=id_doc_url,
            status=KYCStatus.SUBMITTED,
            attempt_number=attempt_number,
            submitted_at=lagos_now(),
        )
        doc = await self.kyc_repo.add(new_doc)

        # Sync ProviderProfile state
        profile_updates: dict = {
            "kyc_status": KYCStatus.SUBMITTED,
            "rejection_reason": None,
            "updated_at": lagos_now(),
        }
        if selfie_url:
            profile_updates["selfie_url"] = selfie_url

        await self.provider_repo.update(profile.id, profile_updates)
        return doc

    @log_error()
    async def approve_kyc(
        self,
        user_id: str,
        document_id: Optional[str] = None,
        reviewer_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> KYCDocument:
        """Approve a KYC submission attempt and mark provider as verified."""
        if document_id:
            doc = await self.kyc_repo.get(document_id)
        else:
            docs = await self.kyc_repo.get_all(
                QueryOptions(filters={"user_id": user_id}, order_by="attempt_number", descending=True, limit=1)
            )
            doc = docs[0] if docs else None

        if not doc or doc.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="KYC submission record not found.",
            )

        if doc.status == KYCStatus.VERIFIED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="KYC verification has already been approved.",
            )

        now = lagos_now()
        meta = dict(doc.meta_data or {})
        meta.update({
            "reviewer_id": reviewer_id,
            "approved_at": now.isoformat(),
            "notes": notes,
        })

        updated_doc = await self.kyc_repo.update(
            doc.id,
            {
                "status": KYCStatus.VERIFIED,
                "reviewed_at": now,
                "rejection_reason": None,
                "meta_data": meta,
                "updated_at": now,
            },
        )

        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if profiles:
            profile = profiles[0]
            profile_updates: dict = {
                "kyc_status": KYCStatus.VERIFIED,
                "verified_at": now,
                "rejection_reason": None,
                "current_onboarding_step": OnboardingStep.GUARANTOR,
                "updated_at": now,
            }

            target_tier = 2
            if self.guarantor_repo:
                guarantors = await self.guarantor_repo.get_all(
                    QueryOptions(filters={"provider_id": user_id})
                )
                if any(g.status == VerificationStatus.PASSED for g in guarantors):
                    target_tier = 3
                    profile_updates["current_onboarding_step"] = OnboardingStep.INTERVIEW

            if self.stats_repo:
                stats_list = await self.stats_repo.get_all(
                    QueryOptions(filters={"user_id": user_id})
                )
                if stats_list:
                    stat = stats_list[0]
                    await self.stats_repo.update(
                        stat.id,
                        {
                            "current_tier": max(stat.current_tier, target_tier),
                            "updated_at": now,
                        },
                    )
                else:
                    await self.stats_repo.add(
                        UserStats(
                            user_id=user_id,
                            current_tier=target_tier,
                            created_at=now,
                            updated_at=now,
                        )
                    )

            await self.provider_repo.update(profile.id, profile_updates)

        return updated_doc or doc

    @log_error()
    async def reject_kyc(
        self,
        user_id: str,
        rejection_reason: str,
        document_id: Optional[str] = None,
        reviewer_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> KYCDocument:
        """Reject a KYC submission attempt and persist the rejection reason."""
        if not rejection_reason or not rejection_reason.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A valid rejection reason must be provided.",
            )

        if document_id:
            doc = await self.kyc_repo.get(document_id)
        else:
            docs = await self.kyc_repo.get_all(
                QueryOptions(filters={"user_id": user_id}, order_by="attempt_number", descending=True, limit=1)
            )
            doc = docs[0] if docs else None

        if not doc or doc.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="KYC submission record not found.",
            )

        now = lagos_now()
        meta = dict(doc.meta_data or {})
        meta.update({
            "reviewer_id": reviewer_id,
            "rejection_reason": rejection_reason,
            "rejected_at": now.isoformat(),
        })
        if metadata:
            meta.update(metadata)

        updated_doc = await self.kyc_repo.update(
            doc.id,
            {
                "status": KYCStatus.FAILED,
                "rejection_reason": rejection_reason,
                "reviewed_at": now,
                "meta_data": meta,
                "updated_at": now,
            },
        )

        profiles = await self.provider_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )
        if profiles:
            await self.provider_repo.update(
                profiles[0].id,
                {
                    "kyc_status": KYCStatus.FAILED,
                    "rejection_reason": rejection_reason,
                    "updated_at": now,
                },
            )

        return updated_doc or doc


def get_kyc_service(
    kyc_repo: Repository[KYCDocument] = Depends(GetRepository(KYCDocument)),
    provider_repo: Repository[ProviderProfile] = Depends(GetRepository(ProviderProfile)),
    user_repo: Repository[User] = Depends(GetRepository(User)),
    guarantor_repo: Repository[ProviderGuarantor] = Depends(GetRepository(ProviderGuarantor)),
    stats_repo: Repository[UserStats] = Depends(GetRepository(UserStats)),
) -> KYCService:
    """Dependency provider injecting repositories into KYCService."""
    return KYCService(
        kyc_repo=kyc_repo,
        provider_repo=provider_repo,
        user_repo=user_repo,
        guarantor_repo=guarantor_repo,
        stats_repo=stats_repo,
    )
