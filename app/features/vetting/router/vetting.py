from typing import List, Optional
from fastapi import APIRouter, Depends, status, HTTPException
from app.core.api_response import BaseAPIResponse
from app.core.deps import GetCurrentUser
from app.core.error_handler import AppErrorHandler
from app.core.models.users import UserType
from app.features.users.schemas import UserResponse
from app.features.vetting.schemas import (
    AddGuarantorRequest,
    AddGuarantorResponse,
    ResubmitGuarantorRequest,
    GuarantorResponse,
    InterviewResponse,
)
from app.features.vetting.vetting_service import VettingService, get_vetting_service
from app.features.vetting.interview_manager_service import (
    InterviewManagerService,
    get_interview_manager_service,
)

router = APIRouter(tags=["Vetting"])


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
    vetting_service: VettingService = Depends(get_vetting_service),
):
    """Add an initial guarantor for attestation."""
    try:
        guarantor = await vetting_service.add_guarantor(
            provider_id=current_user.id, guarantor_data=guarantor_data
        )

        return BaseAPIResponse[AddGuarantorResponse](
            data=AddGuarantorResponse(
                id=guarantor.id,
                status=guarantor.status.value,
                message="Guarantor added successfully. Awaiting attestation.",
            ),
            detail="Guarantor added successfully.",
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add guarantor.",
        )


@router.post(
    "/guarantor/resubmit",
    response_model=BaseAPIResponse[AddGuarantorResponse],
    status_code=status.HTTP_201_CREATED,
)
async def resubmit_guarantor(
    guarantor_data: ResubmitGuarantorRequest,
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)
    ),
    vetting_service: VettingService = Depends(get_vetting_service),
):
    """Resubmit a guarantor if all previous submissions have failed and max attempts limit is not reached."""
    try:
        guarantor = await vetting_service.resubmit_guarantor(
            provider_id=current_user.id, guarantor_data=guarantor_data
        )

        return BaseAPIResponse[AddGuarantorResponse](
            data=AddGuarantorResponse(
                id=guarantor.id,
                status=guarantor.status.value,
                message="Guarantor resubmitted successfully. Awaiting attestation.",
            ),
            detail="Guarantor resubmitted successfully.",
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resubmit guarantor.",
        )


@router.get(
    "/guarantor",
    response_model=BaseAPIResponse[GuarantorResponse],
    status_code=status.HTTP_200_OK,
)
async def get_latest_guarantor(
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)
    ),
    vetting_service: VettingService = Depends(get_vetting_service),
):
    """Retrieve the latest guarantor submission status for the current provider."""
    try:
        guarantor = await vetting_service.get_latest_guarantor(
            provider_id=current_user.id
        )
        if not guarantor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No guarantor submission found.",
            )

        return BaseAPIResponse[GuarantorResponse](
            data=GuarantorResponse.model_validate(guarantor),
            detail="Guarantor status retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve guarantor status.",
        )


@router.get(
    "/guarantor/history",
    response_model=BaseAPIResponse[List[GuarantorResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_guarantor_history(
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)
    ),
    vetting_service: VettingService = Depends(get_vetting_service),
):
    """Retrieve all guarantor submission attempts for the current provider."""
    try:
        guarantors = await vetting_service.get_guarantor_history(
            provider_id=current_user.id
        )
        return BaseAPIResponse[List[GuarantorResponse]](
            data=[GuarantorResponse.model_validate(g) for g in guarantors],
            detail="Guarantor submission history retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve guarantor submission history.",
        )


@router.get(
    "/interview/me",
    response_model=BaseAPIResponse[Optional[InterviewResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_my_interview(
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)
    ),
    interview_manager: InterviewManagerService = Depends(get_interview_manager_service),
):
    """Retrieve scheduled interview details for the current provider."""
    try:
        interview = await interview_manager.get_user_interview(user_id=current_user.id)
        return BaseAPIResponse[Optional[InterviewResponse]](
            data=InterviewResponse.model_validate(interview) if interview else None,
            detail="Interview details retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve interview details.",
        )
