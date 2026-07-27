import secrets
from typing import Dict, List
from fastapi import HTTPException, Depends
from app.core.models.vetting import ProviderQuizResult, ProviderPortfolioMedia, ProviderGuarantor, QuizQuestion
from app.core.models.users import VerificationStatus
from app.core.repository import Repository, GetRepository
from .schemas import AddGuarantorRequest, PortfolioUploadRequest

class VettingService:
    def __init__(
        self, 
        quiz_result_repo: Repository[ProviderQuizResult],
        portfolio_repo: Repository[ProviderPortfolioMedia],
        guarantor_repo: Repository[ProviderGuarantor]
    ):
        self.quiz_result_repo = quiz_result_repo
        self.portfolio_repo = portfolio_repo
        self.guarantor_repo = guarantor_repo

    async def submit_quiz_answers(self, provider_id: str, category_id: str, questions: List[QuizQuestion], answers: Dict[str, str]) -> ProviderQuizResult:
        if not questions:
            raise HTTPException(status_code=400, detail="No questions available for this category.")
        
        correct_count = 0
        for question in questions:
            user_answer = answers.get(question.id)
            if user_answer and user_answer == question.correct_option:
                correct_count += 1
                
        score_percentage = (correct_count / len(questions)) * 100
        status = VerificationStatus.PASSED if score_percentage >= 80.0 else VerificationStatus.FAILED
        
        result = ProviderQuizResult(
            provider_id=provider_id,
            category_id=category_id,
            score_percentage=score_percentage,
            status=status
        )
        
        return await self.quiz_result_repo.add(result)

    async def upload_portfolio(self, provider_id: str, upload_data: PortfolioUploadRequest) -> ProviderPortfolioMedia:
        media = ProviderPortfolioMedia(
            provider_id=provider_id,
            category_id=upload_data.category_id,
            media_url=upload_data.media_url,
            media_type=upload_data.media_type,
            status=VerificationStatus.PENDING
        )
        
        return await self.portfolio_repo.add(media)

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
    quiz_result_repo: Repository[ProviderQuizResult] = Depends(GetRepository(ProviderQuizResult)),
    portfolio_repo: Repository[ProviderPortfolioMedia] = Depends(GetRepository(ProviderPortfolioMedia)),
    guarantor_repo: Repository[ProviderGuarantor] = Depends(GetRepository(ProviderGuarantor))
) -> VettingService:
    return VettingService(
        quiz_result_repo=quiz_result_repo,
        portfolio_repo=portfolio_repo,
        guarantor_repo=guarantor_repo
    )
