import secrets
from fastapi import Depends
from app.core.models.vetting import ProviderGuarantor
from app.core.models.users import VerificationStatus
from app.core.repository import Repository, GetRepository
from .schemas import AddGuarantorRequest


class VettingService:
    def __init__(
        self, 
        guarantor_repo: Repository[ProviderGuarantor]
    ):
        self.guarantor_repo = guarantor_repo

    async def add_guarantor(self, provider_id: str, guarantor_data: AddGuarantorRequest) -> ProviderGuarantor:
        token_hash = secrets.token_urlsafe(32)
        
        guarantor = ProviderGuarantor(
            provider_id=provider_id,
            guarantor_name=guarantor_data.guarantor_name,
            guarantor_phone=guarantor_data.guarantor_phone,
            relationship=guarantor_data.relationship,
            token_hash=token_hash,
            status=VerificationStatus.PENDING
        )
        
        return await self.guarantor_repo.add(guarantor)


def get_vetting_service(
    guarantor_repo: Repository[ProviderGuarantor] = Depends(GetRepository(ProviderGuarantor))
) -> VettingService:
    return VettingService(
        guarantor_repo=guarantor_repo
    )

