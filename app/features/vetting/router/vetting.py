from fastapi import APIRouter, Depends, status, HTTPException
from app.core.api_response import BaseAPIResponse
from app.core.deps import GetCurrentUser
from app.core.error_handler import AppErrorHandler
from app.core.models.users import UserType
from app.features.users.schemas import UserResponse
from app.features.vetting.schemas import (
    AddGuarantorRequest,
    AddGuarantorResponse
)
from app.features.vetting.vetting_service import VettingService, get_vetting_service

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
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add guarantor.",
        )

