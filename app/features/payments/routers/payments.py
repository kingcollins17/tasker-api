import logging
from typing import Any, Dict, Optional

from typing import List
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Request,
    Query,
    HTTPException,
    status,
)
from pydantic import BaseModel
from sqlmodel import select, desc, func, col

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.deps import GetCurrentUser
from app.core.error_handler import AppErrorHandler
from app.core.models.payments import ProviderDebt, PayoutQueue, PayoutStatus
from app.core.models.transactions import Transaction, TransactionStatus, TransactionType
from app.core.models.users import UserType
from app.core.repository import GetRepository, Repository, QueryOptions
from app.features.payments.processors import (
    PaymentWebhookProcessor,
    TransferWebhookProcessor,
    get_payment_processor,
    get_transfer_processor,
)
from app.features.payments.schemas import (
    ProviderDebtSummaryResponse,
    SettleDebtRequest,
    SettleDebtResponse,
    TransactionResponse,
    PayoutQueueResponse,
)
from app.features.payments.services import PaymentService, get_payment_service
from app.features.users.schemas import UserResponse
from app.core.services.logger_service import LoggerService, get_logger_service
from app.core.utils.timer import Timer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["Payments"])


class WebhookPayload(BaseModel):
    event: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


@router.post("/webhooks")
async def process_payment_webhook(
    request: Request,
    payload: WebhookPayload,
    payment_processor: PaymentWebhookProcessor = Depends(get_payment_processor),
    transfer_processor: TransferWebhookProcessor = Depends(get_transfer_processor),
    system_logger: LoggerService = Depends(get_logger_service)
):
    try:
        timer = Timer()
        timer.start()
        # TODO: Implement webhook signature verification
        # e.g., signature = request.headers.get("x-paystack-signature")

        if not payload.event or not payload.data:
            return {"status": "ignored", "message": "Invalid payload structure"}

        logger.info(f"Received payment webhook event: {payload.event}")

        data = payload.data
        reference = str(data.get("reference") or "")
        try:
            amount = float(data.get("amount", 0.0))
        except (TypeError, ValueError):
            amount = 0.0

        metadata = data.get("metadata") or {}
        user_id = metadata.get("user_id")
        task_id = metadata.get("task_id")
        event_type = metadata.get("type")

        if payload.event.startswith("charge."):
            provider_id = metadata.get("provider_id")
            await payment_processor.process(
                payload.event,
                reference=reference,
                amount=amount,
                user_id=user_id,
                task_id=task_id,
                event_type=str(event_type) if event_type is not None else None,
                provider_id=provider_id,
                raw_data=data,
            )
        elif payload.event.startswith("transfer."):
            reason = data.get("reason")
            await transfer_processor.process(
                payload.event,
                reference=reference,
                amount=amount,
                # pyrefly: ignore [bad-argument-type]
                user_id=user_id,
                task_id=task_id,
                reason=reason,
                raw_data=data,
            )
        else:
            logger.info(f"Unhandled webhook event type: {payload.event}")

        await system_logger.metric('process_payment_webhook', timer.stop(), source='payments.process_payment_webhook')
        return {"status": "success", "message": "Webhook processed successfully"}

    except HTTPException as e:
        await system_logger.warn('process_payment_webhook failed', source='payments.process_payment_webhook', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'process_payment_webhook error: {str(e)}', source='payments.process_payment_webhook')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing webhook",
        )


@router.get(
    "/transactions",
    response_model=BaseAPIResponse[PaginatedData[TransactionResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_my_transactions(
    current_user: UserResponse = Depends(GetCurrentUser()),
    transaction_repo: Repository[Transaction] = Depends(GetRepository(Transaction)),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    sort_by: str = Query("created_at"),
    sort_desc: bool = Query(True),
    transaction_type: Optional[TransactionType] = Query(None),
    status_filter: Optional[List[TransactionStatus]] = Query(None, alias="status"),
    system_logger: LoggerService = Depends(get_logger_service)
):
    try:
        timer = Timer()
        timer.start()
        statement = select(Transaction).where(Transaction.user_id == current_user.id)

        if transaction_type:
            statement = statement.where(
                Transaction.transaction_type == transaction_type
            )
        if status_filter:
            statement = statement.where(col(Transaction.status).in_(status_filter))

        # pyrefly: ignore [bad-argument-type]
        count_query = select(func.count(Transaction.id)).where(
            Transaction.user_id == current_user.id
        )
        if transaction_type:
            count_query = count_query.where(
                Transaction.transaction_type == transaction_type
            )
        if status_filter:
            count_query = count_query.where(col(Transaction.status).in_(status_filter))

        total = (await transaction_repo.execute(count_query)).one()

        if hasattr(Transaction, sort_by):
            sort_col = getattr(Transaction, sort_by)
            statement = statement.order_by(desc(sort_col) if sort_desc else sort_col)

        statement = statement.offset((page - 1) * per_page).limit(per_page)

        results = await transaction_repo.execute(statement)
        transactions = list(results.unique().all())

        data = PaginatedData[TransactionResponse](
            items=[TransactionResponse.model_validate(t) for t in transactions],
            total=total,
            page=page,
            per_page=per_page,
        )
        
        await system_logger.metric('list_my_transactions', timer.stop(), source='payments.list_my_transactions')
        return BaseAPIResponse[PaginatedData[TransactionResponse]](
            data=data,
            detail="Transactions retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('list_my_transactions failed', source='payments.list_my_transactions', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'list_my_transactions error: {str(e)}', source='payments.list_my_transactions')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve transactions",
        )


@router.get(
    "/debts/summary",
    response_model=BaseAPIResponse[ProviderDebtSummaryResponse],
    status_code=status.HTTP_200_OK,
)
async def get_my_debt_summary(
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)
    ),
    service: PaymentService = Depends(get_payment_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Fetch provider's current outstanding commission debt summary."""
    try:
        timer = Timer()
        timer.start()
        summary = await service.get_provider_debt_summary(current_user.id)
        
        await system_logger.metric('get_my_debt_summary', timer.stop(), source='payments.get_my_debt_summary')
        return BaseAPIResponse[ProviderDebtSummaryResponse](
            data=summary,
            detail="Debt summary retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('get_my_debt_summary failed', source='payments.get_my_debt_summary', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'get_my_debt_summary error: {str(e)}', source='payments.get_my_debt_summary')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve debt summary.",
        )


@router.post(
    "/debts/settle",
    response_model=BaseAPIResponse[SettleDebtResponse],
    status_code=status.HTTP_200_OK,
)
async def settle_commission_debt(
    payload: SettleDebtRequest,
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_type=UserType.PROVIDER,
            required_phone_verified=True,
            required_email_verified=True,
        )
    ),
    service: PaymentService = Depends(get_payment_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Initialize Paystack payment link for a provider to pay up their commission debt."""
    try:
        timer = Timer()
        timer.start()
        response = await service.initialize_debt_settlement(
            current_user.id, request_amount=payload.amount
        )
        
        await system_logger.metric('settle_commission_debt', timer.stop(), source='payments.settle_commission_debt')
        return BaseAPIResponse[SettleDebtResponse](
            data=response,
            detail="Debt settlement checkout link initialized successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('settle_commission_debt failed', source='payments.settle_commission_debt', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'settle_commission_debt error: {str(e)}', source='payments.settle_commission_debt')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initialize debt settlement.",
        )


@router.get(
    "/customer/payouts",
    response_model=BaseAPIResponse[PaginatedData[PayoutQueueResponse]],
)
async def list_customer_payouts(
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_type=UserType.CUSTOMER,
            required_phone_verified=True,
            required_email_verified=True,
        )
    ),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    sort_by: str = Query("created_at"),
    sort_desc: bool = Query(True),
    status_filter: Optional[List[PayoutStatus]] = Query(None, alias="status"),
    service: PaymentService = Depends(get_payment_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """List out payout queue items where the current user is the customer."""
    try:
        timer = Timer()
        timer.start()
        options = QueryOptions(
            filters={"status": status_filter} if status_filter else {},
            limit=per_page,
            offset=(page - 1) * per_page,
            order_by=sort_by,
            descending=sort_desc,
        )
        data, total = await service.get_customer_payout_queues(current_user.id, options)
        mapped_data = [PayoutQueueResponse.model_validate(p) for p in data]
        
        paginated_data = PaginatedData[PayoutQueueResponse](
            items=mapped_data,
            total=total,
            page=page,
            per_page=per_page,
        )
        
        await system_logger.metric('list_customer_payouts', timer.stop(), source='payments.list_customer_payouts')
        return BaseAPIResponse[PaginatedData[PayoutQueueResponse]](
            data=paginated_data,
            detail="Fetched customer payout queues successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('list_customer_payouts failed', source='payments.list_customer_payouts', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'list_customer_payouts error: {str(e)}', source='payments.list_customer_payouts')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch payout queues.",
        )


@router.get(
    "/customer/payouts/pending-payment",
    response_model=BaseAPIResponse[Optional[PayoutQueueResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_latest_pending_customer_payout(
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_type=UserType.CUSTOMER,
            required_phone_verified=True,
            required_email_verified=True,
        )
    ),
    payout_repo: Repository[PayoutQueue] = Depends(GetRepository(PayoutQueue)),
    system_logger: LoggerService = Depends(get_logger_service),
):
    """Retrieve the customer's most recent PayoutQueue item with status PENDING for checkout completion."""
    try:
        timer = Timer()
        timer.start()

        stmt = (
            select(PayoutQueue)
            .where(PayoutQueue.customer_id == current_user.id)
            .where(PayoutQueue.status == PayoutStatus.PENDING)
            .order_by(desc(PayoutQueue.created_at))
            .limit(1)
        )
        res = await payout_repo.execute(stmt)
        payout: Optional[PayoutQueue] = res.unique().one_or_none()

        data = PayoutQueueResponse.model_validate(payout) if payout else None

        await system_logger.metric(
            "get_latest_pending_customer_payout",
            timer.stop(),
            source="payments.get_latest_pending_customer_payout",
        )
        return BaseAPIResponse[Optional[PayoutQueueResponse]](
            data=data,
            detail=(
                "Latest pending payout retrieved successfully."
                if data
                else "No pending payout found."
            ),
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn(
            "get_latest_pending_customer_payout failed",
            source="payments.get_latest_pending_customer_payout",
            metadata={"detail": e.detail if hasattr(e, "detail") else str(e)},
        )
        raise e
    except Exception as e:
        await system_logger.error(
            f"get_latest_pending_customer_payout error: {str(e)}",
            source="payments.get_latest_pending_customer_payout",
        )
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve latest pending payout.",
        )


@router.post(
    "/customer/payouts/{payout_id}/reinitiate",
    response_model=BaseAPIResponse[PayoutQueueResponse],
)
async def reinitiate_customer_payout(
    payout_id: str,
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
    """Reinitiate checkout for a pending customer payout."""
    try:
        timer = Timer()
        timer.start()
        payout = await service.reinitiate_payout(payout_id, current_user.id)
        
        await system_logger.metric('reinitiate_customer_payout', timer.stop(), source='payments.reinitiate_customer_payout')
        return BaseAPIResponse[PayoutQueueResponse](
            data=PayoutQueueResponse.model_validate(payout),
            detail="Payout payment reinitiated successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('reinitiate_customer_payout failed', source='payments.reinitiate_customer_payout', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'reinitiate_customer_payout error: {str(e)}', source='payments.reinitiate_customer_payout')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reinitiate payout payment.",
        )


@router.get(
    "/provider/payouts",
    response_model=BaseAPIResponse[PaginatedData[PayoutQueueResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_provider_payouts(
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_type=UserType.PROVIDER,
            required_phone_verified=True,
            required_email_verified=True,
        )
    ),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    sort_by: str = Query("created_at"),
    sort_desc: bool = Query(True),
    status_filter: Optional[List[PayoutStatus]] = Query(None, alias="status"),
    service: PaymentService = Depends(get_payment_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """List payout queue items where the current user is the provider."""
    try:
        timer = Timer()
        timer.start()
        options = QueryOptions(
            filters={"status": status_filter} if status_filter else {},
            limit=per_page,
            offset=(page - 1) * per_page,
            order_by=sort_by,
            descending=sort_desc,
        )
        data, total = await service.get_provider_payout_queues(current_user.id, options)
        mapped_data = [PayoutQueueResponse.model_validate(p) for p in data]
        
        paginated_data = PaginatedData[PayoutQueueResponse](
            items=mapped_data,
            total=total,
            page=page,
            per_page=per_page,
        )
        
        await system_logger.metric('list_provider_payouts', timer.stop(), source='payments.list_provider_payouts')
        return BaseAPIResponse[PaginatedData[PayoutQueueResponse]](
            data=paginated_data,
            detail="Fetched provider payout queues successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('list_provider_payouts failed', source='payments.list_provider_payouts', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'list_provider_payouts error: {str(e)}', source='payments.list_provider_payouts')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch payout queues.",
        )


@router.get(
    "/provider/payouts/{payout_id}",
    response_model=BaseAPIResponse[PayoutQueueResponse],
    status_code=status.HTTP_200_OK,
)
async def get_provider_payout(
    payout_id: str,
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_type=UserType.PROVIDER,
            required_phone_verified=True,
            required_email_verified=True,
        )
    ),
    service: PaymentService = Depends(get_payment_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Fetch details of a specific payout queue item for the authenticated provider."""
    try:
        timer = Timer()
        timer.start()
        payout = await service.get_provider_payout(payout_id, current_user.id)
        
        await system_logger.metric('get_provider_payout', timer.stop(), source='payments.get_provider_payout')
        return BaseAPIResponse[PayoutQueueResponse](
            data=PayoutQueueResponse.model_validate(payout),
            detail="Fetched provider payout successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('get_provider_payout failed', source='payments.get_provider_payout', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'get_provider_payout error: {str(e)}', source='payments.get_provider_payout')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch provider payout.",
        )
