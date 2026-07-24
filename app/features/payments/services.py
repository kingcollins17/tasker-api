from typing import List, Optional

from fastapi import Depends, HTTPException, status
from sqlmodel import func, select

from app.core.logging import logger
from app.core.models.payments import DebtReason, ProviderDebt
from app.core.models.tasks import Task
from app.core.models.transactions import Transaction
from app.core.models.users import User
from app.core.repository import GetRepository, Repository
from app.core.services.payment import get_paystack_gateway
from app.features.payments.celery.tasks import (
    process_debt_settlement as process_debt_settlement_task,
    process_provider_payout as process_provider_payout_task,
    process_task_payment as process_task_payment_task,
)
from app.features.payments.schemas import (
    ProviderDebtSummaryResponse,
    SettleDebtResponse,
)


class PaymentService:
    """Service encapsulating payment settlement logic and Celery task invocation proxies."""

    def __init__(
        self,
        task_repo: Repository[Task],
        user_repo: Repository[User],
        transaction_repo: Repository[Transaction],
        debt_repo: Repository[ProviderDebt],
    ):
        self.task_repo = task_repo
        self.user_repo = user_repo
        self.transaction_repo = transaction_repo
        self.debt_repo = debt_repo

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


def get_payment_service(
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    user_repo: Repository[User] = Depends(GetRepository(User)),
    transaction_repo: Repository[Transaction] = Depends(GetRepository(Transaction)),
    debt_repo: Repository[ProviderDebt] = Depends(GetRepository(ProviderDebt)),
) -> PaymentService:
    return PaymentService(
        task_repo=task_repo,
        user_repo=user_repo,
        transaction_repo=transaction_repo,
        debt_repo=debt_repo,
    )
