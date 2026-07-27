from typing import Optional
from datetime import datetime

from app.core.utils.timer import Timer
from app.core.services.logger_service import LoggerService, get_logger_service
from app.core.deps import GetCurrentUser
import logging
from fastapi import APIRouter, Depends, status, HTTPException, Query
from sqlmodel import select, func

from app.core.models.payments import PayoutQueue, PayoutStatus
from app.core.repository import Repository, GetRepository
from app.core.models.users import UserType
from app.features.users.schemas import UserResponse
from app.core.api_response import BaseAPIResponse
from app.core.error_handler import AppErrorHandler
from app.features.payments.services import PaymentService, get_payment_service
from app.features.payments.schemas import CustomerPayoutStatsResponse, ProviderEarningStatsResponse


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments/stats", tags=["Payments Stats"])


@router.get(
    "/customer/payouts",
    response_model=BaseAPIResponse[CustomerPayoutStatsResponse],
)
async def get_customer_payout_stats(
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_type=UserType.CUSTOMER,
            required_phone_verified=True,
            required_email_verified=True,
        )
    ),
    service: PaymentService = Depends(get_payment_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Get payout statistics for the current customer."""
    try:
        timer = Timer()
        timer.start()
        stats = await service.get_customer_payout_stats(current_user.id)
        
        await system_logger.metric('get_customer_payout_stats', timer.stop(), source='payments.stats.get_customer_payout_stats')
        return BaseAPIResponse[CustomerPayoutStatsResponse](
            data=stats,
            detail="Fetched customer payout stats successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('get_customer_payout_stats failed', source='payments.stats.get_customer_payout_stats', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'get_customer_payout_stats error: {str(e)}', source='payments.stats.get_customer_payout_stats')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch customer payout stats.",
        )

@router.get(
    "/provider/earnings",
    response_model=BaseAPIResponse[ProviderEarningStatsResponse],
)
async def get_provider_earning_stats(
    start_date: Optional[datetime] = Query(None, description="Start date for filtering earnings"),
    end_date: Optional[datetime] = Query(None, description="End date for filtering earnings"),
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_type=UserType.PROVIDER,
            required_phone_verified=True,
            required_email_verified=True,
        )
    ),
    payout_queue_repo: Repository[PayoutQueue] = Depends(GetRepository(PayoutQueue)),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Get earning statistics for the current provider."""
    try:
        timer = Timer()
        timer.start()
        stmt = (
            select(func.coalesce(func.sum(PayoutQueue.payout_amount), 0.0))
            .where(PayoutQueue.provider_id == current_user.id)
            .where(PayoutQueue.status == PayoutStatus.COMPLETED)
        )
        
        if start_date:
            stmt = stmt.where(PayoutQueue.created_at >= start_date)
        if end_date:
            stmt = stmt.where(PayoutQueue.created_at <= end_date)
            
        total_earnings = (await payout_queue_repo.execute(stmt)).scalar_one_or_none() or 0.0
        
        stats = ProviderEarningStatsResponse(
            total_earnings=round(float(total_earnings), 2),
        )

        await system_logger.metric('get_provider_earning_stats', timer.stop(), source='payments.stats.get_provider_earning_stats')
        return BaseAPIResponse[ProviderEarningStatsResponse](
            data=stats,
            detail="Fetched provider earning stats successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('get_provider_earning_stats failed', source='payments.stats.get_provider_earning_stats', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'get_provider_earning_stats error: {str(e)}', source='payments.stats.get_provider_earning_stats')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch provider earning stats.",
        )
