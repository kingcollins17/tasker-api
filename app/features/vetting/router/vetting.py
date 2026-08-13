import random
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Dict
from app.core.api_response import BaseAPIResponse
from app.core.deps import GetCurrentUser
from app.core.repository import Repository, QueryOptions, GetRepository
from app.core.error_handler import AppErrorHandler
from app.core.models.users import UserType
from app.core.models.vetting import QuizQuestion
from app.features.users.schemas import UserResponse
from app.features.vetting.schemas import (
    QuizAnswerSubmit, 
    QuizQuestionResponse, 
    QuizResultResponse,
    PortfolioUploadRequest,
    PortfolioMediaResponse,
    AddGuarantorRequest,
    AddGuarantorResponse
)
from app.features.vetting.services import VettingService, get_vetting_service

router = APIRouter(tags=["Vetting"])

@router.get(
    "/quiz/{category_id}",
    response_model=BaseAPIResponse[List[QuizQuestionResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_quiz_questions(
    category_id: str,
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)
    ),
    question_repo: Repository[QuizQuestion] = Depends(GetRepository(QuizQuestion))
):
    """Retrieve random quiz questions for a specific category."""
    try:
        # Note: In a real scenario, you might want to randomly sample questions
        # using the DB (e.g. order_by=func.random(), limit=10). We fetch all 
        # and sample them randomly in memory for simplicity here.
        questions = await question_repo.get_all(QueryOptions(filters={"category_id": category_id}))
        
        # Take at most 10 questions at random to not overload the client
        if len(questions) > 10:
            questions = random.sample(questions, 10)
        else:
            random.shuffle(questions)
        
        response_data = [
            QuizQuestionResponse(
                id=q.id, 
                question_text=q.question_text, 
                options=q.options
            ) 
            for q in questions
        ]
        
        return BaseAPIResponse[List[QuizQuestionResponse]](
            data=response_data,
            detail="Quiz questions retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve quiz questions.",
        )

@router.post(
    "/quiz/{category_id}/submit",
    response_model=BaseAPIResponse[QuizResultResponse],
    status_code=status.HTTP_200_OK,
)
async def submit_quiz_answers(
    category_id: str,
    submission: QuizAnswerSubmit,
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)
    ),
    question_repo: Repository[QuizQuestion] = Depends(GetRepository(QuizQuestion)),
    vetting_service: VettingService = Depends(get_vetting_service)
):
    """Submit quiz answers for auto-grading."""
    try:
        questions = await question_repo.get_all(QueryOptions(filters={"category_id": category_id}))
        result = await vetting_service.submit_quiz_answers(
            provider_id=current_user.id,
            category_id=category_id,
            questions=questions,
            answers=submission.answers
        )
        
        return BaseAPIResponse[QuizResultResponse](
            data=QuizResultResponse(
                score_percentage=result.score_percentage,
                passed=(result.status.value == "passed")
            ),
            detail="Quiz submitted and graded successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process quiz submission.",
        )

@router.post(
    "/portfolio",
    response_model=BaseAPIResponse[PortfolioMediaResponse],
    status_code=status.HTTP_201_CREATED,
)
async def upload_portfolio(
    upload_data: PortfolioUploadRequest,
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)
    ),
    vetting_service: VettingService = Depends(get_vetting_service)
):
    """Upload a portfolio media item (tool photo, past work video, etc.)."""
    try:
        media = await vetting_service.upload_portfolio(
            provider_id=current_user.id,
            upload_data=upload_data
        )
        
        return BaseAPIResponse[PortfolioMediaResponse](
            data=PortfolioMediaResponse(
                id=media.id,
                media_url=media.media_url,
                media_type=media.media_type,
                status=media.status.value
            ),
            detail="Portfolio media uploaded successfully.",
            status_code=status.HTTP_201_CREATED,
        )
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload portfolio media.",
        )

@router.post(
    "/guarantor",
    response_model=BaseAPIResponse[AddGuarantorResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_guarantor(
    guarantor_data: AddGuarantorRequest,
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)
    ),
    vetting_service: VettingService = Depends(get_vetting_service)
):
    """Add a guarantor for attestation."""
    try:
        guarantor = await vetting_service.add_guarantor(
            provider_id=current_user.id,
            guarantor_data=guarantor_data
        )
        
        return BaseAPIResponse[AddGuarantorResponse](
            data=AddGuarantorResponse(
                id=guarantor.id,
                status=guarantor.status.value,
                message="Guarantor added successfully. Awaiting attestation."
            ),
            detail="Guarantor added successfully.",
            status_code=status.HTTP_201_CREATED,
        )
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add guarantor.",
        )
