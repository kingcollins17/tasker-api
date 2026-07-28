from typing import Any
from typing import Dict
from typing import List, Optional

from fastapi import Depends, HTTPException, status
from sqlmodel import func, select

from app.core.logging import logger
from app.core.models.payments import DebtReason, ProviderDebt, PayoutQueue, PayoutStatus
from app.core.models.tasks import Task
from app.core.models.transactions import Transaction
from app.core.models.users import User
from app.core.repository import GetRepository, Repository, QueryOptions
from app.core.services.payment import get_paystack_gateway
from app.core.utils.datetime_helper import lagos_now
from app.features.payments.celery.tasks import (
    process_debt_settlement as process_debt_settlement_task,
    process_provider_payout as process_provider_payout_task,
    process_task_payment as process_task_payment_task,
)
from app.features.payments.schemas import (
    ProviderDebtSummaryResponse,
    SettleDebtResponse,
    CustomerPayoutStatsResponse,
    ProviderEarningStatsResponse,
)


class PaymentService:
    """Service encapsulating payment settlement logic and Celery task invocation proxies."""

    def __init__(
        self,
        task_repo: Repository[Task],
        user_repo: Repository[User],
        transaction_repo: Repository[Transaction],
        debt_repo: Repository[ProviderDebt],
        payout_queue_repo: Repository[PayoutQueue],
    ):
        self.task_repo = task_repo
        self.user_repo = user_repo
        self.transaction_repo = transaction_repo
        self.debt_repo = debt_repo
        self.payout_queue_repo = payout_queue_repo

    # ── Celery Task Proxy Methods ──────────────────────────────────────────

    def trigger_task_payment(
        self, task_id: str, provider_id: str, payment_mode: str
    ) -> None:
        """Proxy method to enqueue process_task_payment Celery task."""
        # pyrefly: ignore [not-callable]
        process_task_payment_task.delay(task_id, provider_id, payment_mode)

    def trigger_provider_payout(
        self, task_id: str, provider_id: str, payout_amount: float
    ) -> None:
        """Proxy method to enqueue process_provider_payout Celery task."""
        # pyrefly: ignore [not-callable]
        process_provider_payout_task.delay(task_id, provider_id, payout_amount)

    def trigger_debt_settlement(
        self, provider_id: str, amount_paid: float, reference: str
    ) -> None:
        """Proxy method to enqueue process_debt_settlement Celery task."""
        # pyrefly: ignore [not-callable]
        process_debt_settlement_task.delay(provider_id, amount_paid, reference)

    # ── Payout Queue Operations ──────────────────────────────────────────

    async def enqueue_payout(
        self,
        provider_id: str,
        payout_amount: float,
        customer_id: Optional[str] = None,
        task_id: Optional[str] = None,
        customer_payment_amount: float = 0.0,
        data: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ) -> PayoutQueue:
        """Enqueue a payout to a provider for future processing."""
        if payout_amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payout amount must be greater than zero.",
            )

        new_payout = PayoutQueue(
            provider_id=provider_id,
            customer_id=customer_id,
            task_id=task_id,
            payout_amount=payout_amount,
            customer_payment_amount=customer_payment_amount,
            data=data,
            description=description,
            status=PayoutStatus.PENDING,
        )
        return await self.payout_queue_repo.add(new_payout)

    # ── Provider Debt Settlement Operations ───────────────────────────────

    async def get_provider_debt_summary(
        self, provider_id: str
    ) -> ProviderDebtSummaryResponse:
        """Calculate total outstanding debt balance for a provider from append-only ledger."""
        stmt_sum = select(func.coalesce(func.sum(ProviderDebt.amount), 0.0)).where(
            ProviderDebt.provider_id == provider_id
        )
        total_owed = float((await self.debt_repo.execute(stmt_sum)).scalar_one_or_none() or 0.0)

        # pyrefly: ignore [bad-argument-type]
        stmt_count = select(func.count(ProviderDebt.id)).where(
            ProviderDebt.provider_id == provider_id
        )
        entry_count = int((await self.debt_repo.execute(stmt_count)).scalar_one_or_none() or 0)

        # Debt balance cannot be negative for summary display (clamp to 0.0 minimum)
        debt_balance = max(0.0, total_owed)
        return ProviderDebtSummaryResponse(
            total_debt_owed=round(debt_balance, 2),
            pending_debts_count=entry_count,
        )

    async def initialize_debt_settlement(
        self,
        provider_id: str,
        request_amount: Optional[float] = None,
    ) -> SettleDebtResponse:
        """Initialize Paystack checkout link for a provider to pay up their accumulated cash commission debt."""
        summary = await self.get_provider_debt_summary(provider_id)
        total_owed = summary.total_debt_owed or 0.0

        if total_owed <= 0.0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You have no outstanding commission debts to pay.",
            )

        if request_amount is not None:
            if request_amount <= 0.0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Settlement amount must be greater than zero.",
                )
            if request_amount > total_owed:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Settlement amount (₦{request_amount:,.2f}) cannot exceed total debt owed (₦{total_owed:,.2f}).",
                )
            amount_to_pay = request_amount
        else:
            amount_to_pay = total_owed

        provider = await self.user_repo.get(provider_id)
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider user account not found.",
            )

        gateway = get_paystack_gateway()
        payment_resp = await gateway.receive_payment(
            email=provider.email,
            amount=amount_to_pay,
            user_id=provider_id,
            metadata={
                "type": "debt_settlement",
                "provider_id": provider_id,
                "amount": amount_to_pay,
            },
        )

        return SettleDebtResponse(
            payment_url=payment_resp.checkout_url,
            reference=payment_resp.reference,
            total_debt_owed=round(total_owed, 2),
            amount_to_pay=round(amount_to_pay, 2),
        )

    async def get_customer_payout_queues(self, customer_id: str, options: QueryOptions):
        """Fetch paginated payout queue items for a given customer id."""
        # Ensure that we inject the customer filter in the options
        options.filters = options.filters or {}
        options.filters["customer_id"] = customer_id
        
        # pyrefly: ignore [bad-argument-type]
        count_stmt = select(func.count(PayoutQueue.id)).where(PayoutQueue.customer_id == customer_id)
        for key, value in options.filters.items():
            if key != "customer_id" and hasattr(PayoutQueue, key):
                count_stmt = count_stmt.where(getattr(PayoutQueue, key) == value)
        total = (await self.payout_queue_repo.execute(count_stmt)).one()

        paginated_data = await self.payout_queue_repo.get_all(options)
        return paginated_data, total

    async def get_provider_payout_queues(self, provider_id: str, options: QueryOptions):
        """Fetch paginated payout queue items for a given provider id."""
        options.filters = options.filters or {}
        options.filters["provider_id"] = provider_id
        
        # pyrefly: ignore [bad-argument-type]
        count_stmt = select(func.count(PayoutQueue.id)).where(PayoutQueue.provider_id == provider_id)
        for key, value in options.filters.items():
            if key != "provider_id" and hasattr(PayoutQueue, key):
                count_stmt = count_stmt.where(getattr(PayoutQueue, key) == value)
        total = (await self.payout_queue_repo.execute(count_stmt)).one()

        paginated_data = await self.payout_queue_repo.get_all(options)
        return paginated_data, total

    async def get_provider_payout(self, payout_id: str, provider_id: str) -> PayoutQueue:
        """Fetch a specific payout queue item for a provider."""
        payout = await self.payout_queue_repo.get(payout_id)
        if not payout or payout.provider_id != provider_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Payout queue item not found.",
            )
        return payout

    async def get_customer_payout_stats(self, customer_id: str) -> CustomerPayoutStatsResponse:
        """Fetch payout statistics for a customer."""
        stmt = (
            # pyrefly: ignore [bad-argument-type]
            select(PayoutQueue.status, func.count(PayoutQueue.id), func.sum(PayoutQueue.customer_payment_amount))
            .where(PayoutQueue.customer_id == customer_id)
            .group_by(PayoutQueue.status)
        )
        results = await self.payout_queue_repo.execute(stmt)
        rows = results.all()
        
        total_payouts = 0
        total_pending = 0
        total_amount_pending = 0.0
        total_completed = 0
        total_amount_completed = 0.0
        
        for row in rows:
            status, count, amount = row
            amount = float(amount or 0.0)
            
            total_payouts += count
            if status == PayoutStatus.PENDING:
                total_pending += count
                total_amount_pending += amount
            elif status in (PayoutStatus.COMPLETED, PayoutStatus.CUSTOMER_PAID):
                total_completed += count
                total_amount_completed += amount
                
        return CustomerPayoutStatsResponse(
            total_payouts=total_payouts,
            total_pending=total_pending,
            total_amount_pending=round(total_amount_pending, 2),
            total_completed=total_completed,
            total_amount_completed=round(total_amount_completed, 2),
        )

    async def reinitiate_payout(
        self,
        payout_id: str,
        customer_id: str
    ) -> PayoutQueue:
        """Reinitiate checkout for a pending customer payout."""
        payout = await self.payout_queue_repo.get(payout_id)
        if not payout or payout.customer_id != customer_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Payout queue item not found.",
            )
            
        if payout.status != PayoutStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Payout is currently {payout.status.value}, cannot reinitiate.",
            )

        task = await self.task_repo.get(payout.task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Associated task not found.",
            )

        customer = await self.user_repo.get(customer_id)
        if not customer:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Customer not found.",
            )

        amount_to_pay = task.customer_total_price
        if not amount_to_pay or amount_to_pay <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task total price is invalid.",
            )

        gateway = get_paystack_gateway()
        payment_resp = await gateway.receive_payment(
            email=customer.email,
            amount=amount_to_pay,
            user_id=customer_id,
            metadata={"task_id": task.id, "type": "task_payment"},
        )

        updated_payout = await self.payout_queue_repo.update(
            payout_id,
            {
                "payment_url": payment_resp.checkout_url,
                "reference": payment_resp.reference,
                "url_generated_at": lagos_now(),
            },
        )
        # pyrefly: ignore [bad-return]
        return updated_payout

def get_payment_service(
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    user_repo: Repository[User] = Depends(GetRepository(User)),
    transaction_repo: Repository[Transaction] = Depends(GetRepository(Transaction)),
    debt_repo: Repository[ProviderDebt] = Depends(GetRepository(ProviderDebt)),
    payout_queue_repo: Repository[PayoutQueue] = Depends(GetRepository(PayoutQueue)),
) -> PaymentService:
    return PaymentService(
        task_repo=task_repo,
        user_repo=user_repo,
        transaction_repo=transaction_repo,
        debt_repo=debt_repo,
        payout_queue_repo=payout_queue_repo,
    )
