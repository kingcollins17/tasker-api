from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc
from sqlmodel import col, select

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.deps.auth import GetCurrentAdmin
from app.core.error_handler import AppErrorHandler
from app.core.models.admins import AdminUser
from app.core.models.payments import PayoutQueue, PayoutStatus
from app.core.models.transactions import Transaction, TransactionStatus, TransactionType
from app.core.repository import GetRepository, Repository
from app.core.services.logger_service import LoggerService, get_logger_service
from app.core.utils.timer import Timer
from app.features.payments.payment_service import PaymentService, get_payment_service
from app.features.payments.schemas import PayoutQueueResponse, TransactionResponse

router = APIRouter(prefix="/admin/payments", tags=["Payments - Admin"])


@router.get(
    "/transactions",
    response_model=BaseAPIResponse[PaginatedData[TransactionResponse]],
    status_code=status.HTTP_200_OK,
    summary="List transactions for admin inspection",
)
async def list_admin_transactions(
    transaction_id: Optional[str] = Query(None, alias="id", description="Filter transactions by transaction ID"),
    user_id: Optional[str] = Query(None, description="Filter transactions by user ID"),
    task_id: Optional[str] = Query(None, description="Filter transactions by task ID"),
    transaction_type: Optional[TransactionType] = Query(
        None, description="Filter transactions by type (e.g., TASK_PAYMENT, PROVIDER_PAYOUT)"
    ),
    tx_status: Optional[TransactionStatus] = Query(
        None, alias="status", description="Filter transactions by status (PENDING, SUCCESS, FAILED)"
    ),
    reference: Optional[str] = Query(None, description="Filter transactions by gateway reference"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    transaction_repo: Repository[Transaction] = Depends(GetRepository(Transaction)),
    system_logger: LoggerService = Depends(get_logger_service),
):
    """List transactions for admin inspection with inlined retrieval queries and pagination."""
    try:
        timer = Timer()
        timer.start()

        query = select(Transaction)
        conditions = []
        if transaction_id:
            conditions.append(Transaction.id == transaction_id)
        if user_id:
            conditions.append(Transaction.user_id == user_id)
        if task_id:
            conditions.append(Transaction.task_id == task_id)
        if transaction_type:
            conditions.append(Transaction.transaction_type == transaction_type)
        if tx_status:
            conditions.append(Transaction.status == tx_status)
        if reference:
            conditions.append(col(Transaction.reference).ilike(f"%{reference.strip()}%"))

        if conditions:
            query = query.where(*conditions)

        query = query.order_by(desc(col(Transaction.created_at))).offset((page - 1) * per_page).limit(per_page)
        res = await transaction_repo.execute(query)
        items = list(res.unique().all())
        total = len(items)

        paginated_data = PaginatedData[TransactionResponse](
            items=[TransactionResponse.model_validate(t) for t in items],
            total=total,
            page=page,
            per_page=per_page,
        )

        await system_logger.metric(
            "list_admin_transactions",
            timer.stop(),
            source="payments.admin.list_transactions",
        )
        return BaseAPIResponse[PaginatedData[TransactionResponse]](
            data=paginated_data,
            detail="Transactions retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        await system_logger.error(
            f"list_admin_transactions error: {str(e)}",
            source="payments.admin.list_transactions",
        )
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve transactions.",
        )


@router.get(
    "/payouts",
    response_model=BaseAPIResponse[PaginatedData[PayoutQueueResponse]],
    status_code=status.HTTP_200_OK,
    summary="List payouts for admin inspection",
)
async def list_admin_payouts(
    payout_id: Optional[str] = Query(None, alias="id", description="Filter payouts by payout ID"),
    provider_id: Optional[str] = Query(None, description="Filter payouts by provider ID"),
    customer_id: Optional[str] = Query(None, description="Filter payouts by customer ID"),
    task_id: Optional[str] = Query(None, description="Filter payouts by task ID"),
    payout_status: Optional[PayoutStatus] = Query(
        None, alias="status", description="Filter payouts by status (e.g., CUSTOMER_PAID, TRANSFER_INITIATED, COMPLETED)"
    ),
    reference: Optional[str] = Query(None, description="Filter payouts by payout reference"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    payout_queue_repo: Repository[PayoutQueue] = Depends(GetRepository(PayoutQueue)),
    system_logger: LoggerService = Depends(get_logger_service),
):
    """List payouts for admin inspection with inlined retrieval queries and pagination."""
    try:
        timer = Timer()
        timer.start()

        query = select(PayoutQueue)
        conditions = []
        if payout_id:
            conditions.append(PayoutQueue.id == payout_id)
        if provider_id:
            conditions.append(PayoutQueue.provider_id == provider_id)
        if customer_id:
            conditions.append(PayoutQueue.customer_id == customer_id)
        if task_id:
            conditions.append(PayoutQueue.task_id == task_id)
        if payout_status:
            conditions.append(PayoutQueue.status == payout_status)
        if reference:
            conditions.append(col(PayoutQueue.reference).ilike(f"%{reference.strip()}%"))

        if conditions:
            query = query.where(*conditions)

        query = query.order_by(desc(col(PayoutQueue.created_at))).offset((page - 1) * per_page).limit(per_page)
        res = await payout_queue_repo.execute(query)
        items = list(res.unique().all())
        total = len(items)

        paginated_data = PaginatedData[PayoutQueueResponse](
            items=[PayoutQueueResponse.model_validate(p) for p in items],
            total=total,
            page=page,
            per_page=per_page,
        )

        await system_logger.metric(
            "list_admin_payouts",
            timer.stop(),
            source="payments.admin.list_payouts",
        )
        return BaseAPIResponse[PaginatedData[PayoutQueueResponse]](
            data=paginated_data,
            detail="Payouts retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        await system_logger.error(
            f"list_admin_payouts error: {str(e)}",
            source="payments.admin.list_payouts",
        )
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve payouts.",
        )


@router.post(
    "/payouts/{payout_id}/transfer",
    response_model=BaseAPIResponse[PayoutQueueResponse],
    status_code=status.HTTP_200_OK,
    summary="Trigger provider payout transfer for a payout in CUSTOMER_PAID status",
)
async def trigger_payout_transfer(
    payout_id: str,
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    payout_queue_repo: Repository[PayoutQueue] = Depends(GetRepository(PayoutQueue)),
    service: PaymentService = Depends(get_payment_service),
    system_logger: LoggerService = Depends(get_logger_service),
):
    """Trigger provider payout transfer when a payout object is in CUSTOMER_PAID status."""
    try:
        timer = Timer()
        timer.start()

        payout = await payout_queue_repo.get(payout_id)
        if not payout:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Payout with ID '{payout_id}' not found.",
            )

        if payout.status != PayoutStatus.CUSTOMER_PAID:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Payout status is '{payout.status.value}'. Provider payout transfer can only be triggered when payout is in 'CUSTOMER_PAID' status.",
            )

        if not payout.task_id or not payout.provider_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payout record is missing associated task_id or provider_id.",
            )

        await service.process_provider_payout(
            task_id=payout.task_id,
            provider_id=payout.provider_id,
        )

        updated_payout = await payout_queue_repo.get(payout_id)
        final_payout = updated_payout or payout

        await system_logger.metric(
            "trigger_payout_transfer",
            timer.stop(),
            source="payments.admin.trigger_payout_transfer",
        )
        return BaseAPIResponse[PayoutQueueResponse](
            data=PayoutQueueResponse.model_validate(final_payout),
            detail="Provider payout transfer triggered successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        await system_logger.error(
            f"trigger_payout_transfer error: {str(e)}",
            source="payments.admin.trigger_payout_transfer",
        )
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to trigger payout transfer.",
        )
