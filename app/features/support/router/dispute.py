from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.api_response import BaseAPIResponse
from app.core.database import get_session
from app.core.deps import GetCurrentUser
from app.core.error_handler import AppErrorHandler
from app.core.models.users import UserType
from app.features.support.schemas import DisputeCreate, DisputeResponse, SupportCaseResponse
from app.features.support.services.case_service import SupportCaseService
from app.features.support.services.dispute_service import DisputeService
from app.features.users.schemas import UserResponse

router = APIRouter()


@router.post("", response_model=BaseAPIResponse[DisputeResponse], status_code=status.HTTP_201_CREATED)
async def open_dispute(
    schema: DisputeCreate,
    current_user: UserResponse = Depends(GetCurrentUser()),
    session: AsyncSession = Depends(get_session),
):
    try:
        service = DisputeService(session)
        is_customer = current_user.type == UserType.CUSTOMER
        case, dispute = await service.open_dispute(
            user_id=current_user.id,
            is_customer=is_customer,
            schema=schema,
        )
        return BaseAPIResponse.success_response(
            data=DisputeResponse.model_validate(dispute),
            message="Dispute opened successfully",
        )
    except HTTPException as e:
        raise e
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to open dispute",
        )


@router.get("/{case_id}", response_model=BaseAPIResponse[DisputeResponse])
async def get_dispute(
    case_id: str,
    current_user: UserResponse = Depends(GetCurrentUser()),
    session: AsyncSession = Depends(get_session),
):
    try:
        case_service = SupportCaseService(session)
        case = await case_service.get_case(case_id)

        dispute_service = DisputeService(session)
        dispute = await dispute_service.get_dispute(case_id)
        if not dispute:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Dispute not found",
            )

        if case and (case.customer_id != current_user.id and case.provider_id != current_user.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unauthorized access to dispute",
            )

        return BaseAPIResponse.success_response(
            data=DisputeResponse.model_validate(dispute),
            message="Dispute details retrieved successfully",
        )
    except HTTPException as e:
        raise e
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve dispute details",
        )
