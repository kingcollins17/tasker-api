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
from sqlmodel import select, desc, func

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.deps import GetCurrentUser
from app.core.error_handler import AppErrorHandler
from app.core.models.transactions import Transaction, TransactionStatus, TransactionType
from app.core.models.users import UserType
from app.core.repository import GetRepository, Repository
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
)
from app.features.payments.services import PaymentService, get_payment_service
from app.features.users.schemas import UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["Payments"])


class WebhookPayload(BaseModel):
    event: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


@router.post("/webhooks")
async def process_payment_webhook(
    request: Request,
    payload: WebhookPayload,
    background_tasks: BackgroundTasks,
    payment_processor: PaymentWebhookProcessor = Depends(get_payment_processor),
    transfer_processor: TransferWebhookProcessor = Depends(get_transfer_processor),
):
    try:
        # TODO: Implement webhook signature verification
        # e.g., signature = request.headers.get("x-paystack-signature")

        if not payload.event or not payload.data:
            return {"status": "ignored", "message": "Invalid payload structure"}

        logger.info(f"Received payment webhook event: {payload.event}")

        if payload.event.startswith("charge."):
            background_tasks.add_task(
                payment_processor.process, payload.event, payload.data
            )
        elif payload.event.startswith("transfer."):
            background_tasks.add_task(
                transfer_processor.process, payload.event, payload.data
            )
        else:
            logger.info(f"Unhandled webhook event type: {payload.event}")

        return {"status": "success", "message": "Webhook processed successfully"}

    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing webhook",
        )


@router.get("/transactions", response_model=BaseAPIResponse[PaginatedData[TransactionResponse]], status_code=status.HTTP_200_OK)
async def list_my_transactions(
    current_user: UserResponse = Depends(GetCurrentUser()),
    transaction_repo: Repository[Transaction] = Depends(GetRepository(Transaction)),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    sort_by: str = Query("created_at"),
    sort_desc: bool = Query(True),
    transaction_type: Optional[TransactionType] = Query(None),
    status_filter: Optional[TransactionStatus] = Query(None, alias="status"),
):
    try:
        statement = select(Transaction).where(Transaction.user_id == current_user.id)

        if transaction_type:
            statement = statement.where(
                Transaction.transaction_type == transaction_type
            )
        if status_filter:
            statement = statement.where(Transaction.status == status_filter)

        count_query = select(func.count(Transaction.id)).where(Transaction.user_id == current_user.id)
        if transaction_type:
            count_query = count_query.where(Transaction.transaction_type == transaction_type)
        if status_filter:
            count_query = count_query.where(Transaction.status == status_filter)
            
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
        return BaseAPIResponse[PaginatedData[TransactionResponse]](
            data=data,
            detail="Transactions retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve transactions")


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
):
    """Fetch provider's current outstanding commission debt summary."""
    try:
        summary = await service.get_provider_debt_summary(current_user.id)
        return BaseAPIResponse[ProviderDebtSummaryResponse](
            data=summary,
            detail="Debt summary retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
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
):
    """Initialize Paystack payment link for a provider to pay up their commission debt."""
    try:
        response = await service.initialize_debt_settlement(
            current_user.id, request_amount=payload.amount
        )
        return BaseAPIResponse[SettleDebtResponse](
            data=response,
            detail="Debt settlement checkout link initialized successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initialize debt settlement.",
        )
