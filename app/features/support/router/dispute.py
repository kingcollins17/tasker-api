from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import col, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.api_response import BaseAPIResponse
from app.core.database import get_session
from app.core.deps import GetCurrentUser
from app.core.error_handler import AppErrorHandler
from app.core.models.support import Dispute, SupportCase
from app.core.models.users import UserType
from app.features.support.schemas import DisputeCreate, DisputeResponse
from app.features.support.services.dispute_service import (
    DisputeService,
    get_dispute_service,
)
from app.features.users.schemas import UserResponse

router = APIRouter()


@router.post("", response_model=BaseAPIResponse[DisputeResponse], status_code=status.HTTP_201_CREATED)
async def open_dispute(
    schema: DisputeCreate,
    current_user: UserResponse = Depends(GetCurrentUser()),
    service: DisputeService = Depends(get_dispute_service),
):
    try:
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
    except HTTPException:
        raise
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
        stmt_case = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        case = (await session.exec(stmt_case)).first()

        stmt_disp = select(Dispute).where(
            or_(
                col(Dispute.case_id) == case_id,
                col(Dispute.id) == case_id,
            )
        )
        dispute = (await session.exec(stmt_disp)).first()
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
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve dispute details",
        )
