from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, status
from sqlmodel import func, select, col

from app.core.logging import logger
from app.core.models.notifications import NotificationType
from app.core.models.payments import DebtReason, ProviderDebt, PayoutQueue, PayoutStatus
from app.core.models.tasks import PaymentMode, PaymentStatus, Task
from app.core.models.transactions import Transaction, TransactionStatus, TransactionType
from app.core.models.users import User
from app.core.repository import GetRepository, Repository, QueryOptions
from app.core.services.payment import get_paystack_gateway, PaystackPaymentGateway
from app.core.utils.datetime_helper import lagos_now, now
from app.features.notifications.services import (
    NotificationService,
    get_notification_service,
)
from app.features.payments.schemas import (
    ProviderDebtSummaryResponse,
    SettleDebtResponse,
    CustomerPayoutStatsResponse,
    ProviderEarningStatsResponse,
)
from app.features.payments.transfer_service import (
    TransferService,
    get_transfer_service,
    get_transfer_service_manual,
)


class PaymentService:
    """Service encapsulating all payment settlement logic.

    Methods are real async implementations that can be called directly from
    Celery async helpers or FastAPI endpoints.
    """

    def __init__(
        self,
        task_repo: Repository[Task],
        user_repo: Repository[User],
        transaction_repo: Repository[Transaction],
        debt_repo: Repository[ProviderDebt],
        payout_queue_repo: Repository[PayoutQueue],
        notification_service: NotificationService,
        payment_gateway: PaystackPaymentGateway,
        transfer_service: "TransferService",
    ):
        self.task_repo = task_repo
        self.user_repo = user_repo
        self.transaction_repo = transaction_repo
        self.debt_repo = debt_repo
        self.payout_queue_repo = payout_queue_repo
        self.notification_service = notification_service
        self.payment_gateway = payment_gateway
        self.transfer_service = transfer_service

    # ── Core Payment Processing Methods ────────────────────────────────────

    async def process_task_payment(
        self,
        task_id: str,
        provider_id: str,
        payment_mode: str = "cash",
    ) -> None:
        """Handle task payment processing after task completion.

        Steps:
        1. Get task amount/pricing.
        2. Create PayoutQueue object (COMPLETED for cash, PENDING for online).
        3. Cash path: record debt ledger entry for platform fee, notify provider.
        4. Online path: generate Paystack payment link, update payout queue.
        5. Online path: notify customer to make payment via email, push, in_app.
        """
        task = await self.task_repo.get(task_id)
        if not task:
            logger.error(f"process_task_payment: task {task_id} not found")
            return

        mode = (
            PaymentMode(payment_mode)
            if payment_mode in ("cash", "online")
            else PaymentMode.CASH
        )
        task.payment_mode = mode

        if mode == PaymentMode.CASH:
            await self._process_cash_payment(task, provider_id)
        elif mode == PaymentMode.ONLINE:
            await self._process_online_payment(task, provider_id)

        await self.task_repo.add(task)

    async def process_provider_payout(
        self,
        task_id: str,
        provider_id: str,
    ) -> None:
        """Prepare provider payout with debt offset, then create a durable Transfer.

        Steps:
        1. Fetch associated Task and existing PayoutQueue entry for task & provider.
        2. Resolve gross payout amount from PayoutQueue.payout_amount or task.provider_payout.
        3. If no PayoutQueue entry exists, insert a new PayoutQueue entry.
        4. Calculate net debt balance and offset pending provider debt.
        5. Create a durable Transfer record for the net remaining payout.
        6. Enqueue the Transfer for background processing via Celery.
        """
        print(f"[DEBUG process_provider_payout] START - task_id: {task_id}, provider_id: {provider_id}")
        from app.features.payments.celery.transfer_tasks import process_transfer_task

        # Fetch task details for payout calculation and customer ID
        task = await self.task_repo.get(task_id)
        print(f"[DEBUG process_provider_payout] Fetched task: {task.id if task else None}")

        # Check for existing payout queue entry
        payouts = await self.payout_queue_repo.get_all(
            QueryOptions(filters={"task_id": task_id, "provider_id": provider_id}),
            use_unique=True,
        )
        payout_obj: Optional[PayoutQueue] = payouts[0] if payouts else None
        print(f"[DEBUG process_provider_payout] Found payout_obj: {payout_obj.id if payout_obj else None}, status: {payout_obj.status if payout_obj else None}")

        if not payout_obj:
            msg = f"process_provider_payout: No PayoutQueue record found for task {task_id} and provider {provider_id}. Skipping payout."
            print(f"[DEBUG process_provider_payout] {msg}")
            logger.warning(
               msg)
            return

        if payout_obj.status != PayoutStatus.CUSTOMER_PAID:
            msg = f"process_provider_payout: PayoutQueue {payout_obj.id} status is {payout_obj.status.value}, expected CUSTOMER_PAID. Skipping payout."
            print(f"[DEBUG process_provider_payout] {msg}")
            logger.warning(msg)
            return

        resolved_payout_amount = payout_obj.payout_amount or (
            task.provider_payout if task and task.provider_payout else 0.0
        )
        print(f"[DEBUG process_provider_payout] resolved_payout_amount: {resolved_payout_amount}")

        # 1. Calculate net debt balance using SUM(amount) from append-only ledger
        stmt = select(func.coalesce(func.sum(ProviderDebt.amount), 0.0)).where(
            ProviderDebt.provider_id == provider_id
        )
        total_debt = float((await self.debt_repo.execute(stmt)).one_or_none() or 0.0)
        print(f"[DEBUG process_provider_payout] total_debt: {total_debt}")

        remaining_payout = resolved_payout_amount
        debt_offset = 0.0

        if total_debt > 0.0:
            debt_offset = min(resolved_payout_amount, total_debt)
            remaining_payout = resolved_payout_amount - debt_offset
            print(f"[DEBUG process_provider_payout] Offsetting debt. debt_offset: {debt_offset}, remaining_payout: {remaining_payout}")

            # Append negative (-) debt ledger entry for payout offset
            offset_entry = ProviderDebt(
                provider_id=provider_id,
                task_id=task_id,
                amount=-debt_offset,
                reason=DebtReason.PAYOUT_OFFSET,
                description=f"Automated debt offset from online task payout #{task_id}",
            )
            await self.debt_repo.add(offset_entry)
            print(f"[DEBUG process_provider_payout] Added offset ProviderDebt entry")

            # Log debt settlement transaction for revenue audit
            debt_settle_tx = Transaction(
                amount=debt_offset,
                transaction_type=TransactionType.DEBT_SETTLEMENT,
                status=TransactionStatus.SUCCESS,
                user_id=provider_id,
                task_id=task_id,
                metadata_info={
                    "source": "payout_offset",
                    "debt_offset": debt_offset,
                },
            )
            await self.transaction_repo.add(debt_settle_tx)
            print(f"[DEBUG process_provider_payout] Added debt settlement Transaction")

        # 2. Create durable Transfer record for net remaining payout
        if remaining_payout > 0 and payout_obj:
            print(f"[DEBUG process_provider_payout] Creating transfer for remaining_payout: {remaining_payout}")
            transfer = await self.transfer_service.create_transfer(
                payment_id=payout_obj.id,
                task_id=task_id,
                provider_id=provider_id,
                amount=remaining_payout,
            )
            print(f"[DEBUG process_provider_payout] Created transfer: {transfer.id if transfer else None}. Enqueuing process_transfer_task Celery task...")
            # Enqueue for background processing
            # pyrefly: ignore [not-callable]
            process_transfer_task.delay(transfer.id)
        else:
            print(f"[DEBUG process_provider_payout] Skipping transfer creation (remaining_payout={remaining_payout})")

        # 3. Update payout queue status
        if payout_obj:
            print(f"[DEBUG process_provider_payout] Updating PayoutQueue status to TRANSFER_INITIATED")
            await self.payout_queue_repo.update(
                payout_obj.id,
                {
                    "payout_amount": resolved_payout_amount,
                    "status": PayoutStatus.TRANSFER_INITIATED,
                },
            )

        # 4. Update task payment status
        if task:
            print(f"[DEBUG process_provider_payout] Updating Task payment_status to TRANSFER_INITIATED")
            task.payment_status = PaymentStatus.TRANSFER_INITIATED
            await self.task_repo.add(task)

        print(f"[DEBUG process_provider_payout] END - Payout processed for provider {provider_id} on task {task_id}")
        logger.info(
            f"Processed payout for provider {provider_id} on task {task_id}: gross=₦{resolved_payout_amount:,.2f}, "
            f"debt_offset=₦{debt_offset:,.2f}, net_transfer=₦{remaining_payout:,.2f}"
        )


    # ── Cash / Online Payment Helpers ──────────────────────────────────────

    async def _process_cash_payment(self, task: Task, provider_id: str) -> None:
        """Handle the cash payment path: debt ledger entry + completed payout queue + notification."""
        platform_fee = task.platform_fee or 0.0
        task.payment_status = PaymentStatus.CASH_PAID

        if platform_fee > 0.0:
            # Append positive (+) debt ledger entry for cash task commission
            provider_debt = ProviderDebt(
                provider_id=provider_id,
                task_id=task.id,
                amount=platform_fee,
                reason=DebtReason.CASH_TASK_COMMISSION,
                description=f"Platform fee for cash task #{task.id}",
            )
            await self.debt_repo.add(provider_debt)
            logger.info(
                f"Recorded cash debt entry (+₦{platform_fee:,.2f}) for provider {provider_id} on task {task.id}"
            )

        if task.provider_payout and task.provider_payout > 0:
            payout = PayoutQueue(
                provider_id=provider_id,
                task_id=task.id,
                customer_id=task.customer_id,
                payout_amount=task.provider_payout,
                customer_payment_amount=task.customer_total_price or 0.0,
                status=PayoutStatus.COMPLETED,
                description=f"Automated payout queue (CASH) for task {task.id}",
            )
            await self.payout_queue_repo.add(payout)

        # Notify customer that provider has been paid in cash for the completed task
        if task.customer_id:
            amt_fmt = (
                f"₦{task.customer_total_price:,.2f}"
                if task.customer_total_price
                else ""
            )
            await self.notification_service.notify(
                recepients=[task.customer_id],
                title="Task Completed — Paid in Cash",
                body=f"Your task '{task.title}' is completed. Payment of {amt_fmt} was settled in cash.",
                type=NotificationType.PAYMENT_RECEIVED,
                channels=["IN_APP", "PUSH"],
                data={
                    "task_id": task.id,
                    "payment_mode": "cash",
                    "amount": task.customer_total_price,
                    "type": "cash_payment_confirmed",
                },
            )

    async def _process_online_payment(self, task: Task, provider_id: str) -> None:
        """Handle the online payment path: generate payment link + pending payout queue + notification."""
        customer = (
            await self.user_repo.get(task.customer_id) if task.customer_id else None
        )
        customer_email = customer.email if customer else "customer@example.com"

        gateway = self.payment_gateway
        payment_resp = await gateway.receive_payment(
            email=customer_email,
            amount=task.customer_total_price or 0.0,
            user_id=task.customer_id,
            metadata={
                "task_id": task.id,
                "user_id": task.customer_id,
                "type": "task_payment",
            },
        )
        task.payment_url = payment_resp.checkout_url
        task.payment_status = PaymentStatus.PAYMENT_REQUESTED

        if task.provider_payout and task.provider_payout > 0:
            payout = PayoutQueue(
                provider_id=provider_id,
                task_id=task.id,
                customer_id=task.customer_id,
                payout_amount=task.provider_payout,
                customer_payment_amount=task.customer_total_price or 0.0,
                payment_url=payment_resp.checkout_url,
                reference=payment_resp.reference,
                url_generated_at=now(),
                status=PayoutStatus.PENDING,
                description=f"Automated payout queue for task {task.id}",
            )
            await self.payout_queue_repo.add(payout)

        # Notify customer to make payment via email, push, and in-app
        if task.customer_id:
            purl = payment_resp.checkout_url or ""
            amt_fmt = (
                f"₦{task.customer_total_price:,.2f}"
                if task.customer_total_price
                else ""
            )
            await self.notification_service.notify(
                recepients=[task.customer_id],
                title="Payment Requested for Completed Task",
                body=f"Your task '{task.title}' is completed. Tap to pay {amt_fmt} online.",
                type=NotificationType.PAYMENT_REQUESTED,
                channels=["IN_APP", "PUSH", "EMAIL"],
                data={
                    "task_id": task.id,
                    "payment_url": purl,
                    "amount": task.customer_total_price,
                    "type": "payment_request",
                },
            )
        logger.info(
            f"Initialized online payment checkout link for task {task.id}: {task.payment_url}"
        )

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
        total_owed = float(
            (await self.debt_repo.execute(stmt_sum)).one_or_none() or 0.0
        )

        # pyrefly: ignore [bad-argument-type]
        stmt_count = select(func.count(ProviderDebt.id)).where(
            ProviderDebt.provider_id == provider_id
        )
        entry_count = int((await self.debt_repo.execute(stmt_count)).one_or_none() or 0)

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

        gateway = self.payment_gateway
        payment_resp = await gateway.receive_payment(
            email=provider.email,
            amount=amount_to_pay,
            user_id=provider_id,
            metadata={
                "type": "debt_settlement",
                "provider_id": provider_id,
                "user_id": provider_id,
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
        count_stmt = select(func.count(PayoutQueue.id)).where(
            PayoutQueue.customer_id == customer_id
        )
        for key, value in options.filters.items():
            if key != "customer_id" and hasattr(PayoutQueue, key):
                if isinstance(value, (list, tuple, set)):
                    count_stmt = count_stmt.where(
                        col(getattr(PayoutQueue, key)).in_(value)
                    )
                else:
                    count_stmt = count_stmt.where(getattr(PayoutQueue, key) == value)
        total = (await self.payout_queue_repo.execute(count_stmt)).one()

        paginated_data = await self.payout_queue_repo.get_all(options, use_unique=True)
        return paginated_data, total

    async def get_provider_payout_queues(self, provider_id: str, options: QueryOptions):
        """Fetch paginated payout queue items for a given provider id."""
        options.filters = options.filters or {}
        options.filters["provider_id"] = provider_id

        # pyrefly: ignore [bad-argument-type]
        count_stmt = select(func.count(PayoutQueue.id)).where(
            PayoutQueue.provider_id == provider_id
        )
        for key, value in options.filters.items():
            if key != "provider_id" and hasattr(PayoutQueue, key):
                if isinstance(value, (list, tuple, set)):
                    count_stmt = count_stmt.where(
                        col(getattr(PayoutQueue, key)).in_(value)
                    )
                else:
                    count_stmt = count_stmt.where(getattr(PayoutQueue, key) == value)
        total = (await self.payout_queue_repo.execute(count_stmt)).one()

        paginated_data = await self.payout_queue_repo.get_all(options, use_unique=True)
        return paginated_data, total

    async def get_provider_payout(
        self, payout_id: str, provider_id: str
    ) -> PayoutQueue:
        """Fetch a specific payout queue item for a provider."""
        payout = await self.payout_queue_repo.get(payout_id)
        if not payout or payout.provider_id != provider_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Payout queue item not found.",
            )
        return payout

    async def get_customer_payout_stats(
        self, customer_id: str
    ) -> CustomerPayoutStatsResponse:
        """Fetch payout statistics for a customer."""
        stmt = (
            # pyrefly: ignore [bad-argument-type]
            select(
                PayoutQueue.status,
                func.count(col(PayoutQueue.id)),
                func.sum(PayoutQueue.customer_payment_amount),
            )
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
            payout_status, count, amount = row
            amount = float(amount or 0.0)

            total_payouts += count
            if payout_status == PayoutStatus.PENDING:
                total_pending += count
                total_amount_pending += amount
            elif payout_status in (PayoutStatus.COMPLETED, PayoutStatus.CUSTOMER_PAID):
                total_completed += count
                total_amount_completed += amount

        return CustomerPayoutStatsResponse(
            total_payouts=total_payouts,
            total_pending=total_pending,
            total_amount_pending=round(total_amount_pending, 2),
            total_completed=total_completed,
            total_amount_completed=round(total_amount_completed, 2),
        )

    async def reinitiate_payout(self, payout_id: str, customer_id: str) -> PayoutQueue:
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

        gateway = self.payment_gateway
        payment_resp = await gateway.receive_payment(
            email=customer.email,
            amount=amount_to_pay,
            user_id=customer_id,
            metadata={
                "task_id": task.id,
                "user_id": customer_id,
                "type": "task_payment",
            },
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
    notification_service: NotificationService = Depends(get_notification_service),
    payment_gateway: PaystackPaymentGateway = Depends(get_paystack_gateway),
    transfer_service: "TransferService" = Depends(get_transfer_service),
) -> PaymentService:
    return PaymentService(
        task_repo=task_repo,
        user_repo=user_repo,
        transaction_repo=transaction_repo,
        debt_repo=debt_repo,
        payout_queue_repo=payout_queue_repo,
        notification_service=notification_service,
        payment_gateway=payment_gateway,
        transfer_service=transfer_service,
    )


def get_payment_service_manual(session) -> PaymentService:
    """Factory for constructing PaymentService outside of FastAPI dependency injection (e.g. Celery tasks)."""
    from app.features.notifications.services import get_notification_service_manual

    return PaymentService(
        task_repo=Repository(Task, session),
        user_repo=Repository(User, session),
        transaction_repo=Repository(Transaction, session),
        debt_repo=Repository(ProviderDebt, session),
        payout_queue_repo=Repository(PayoutQueue, session),
        notification_service=get_notification_service_manual(session),
        payment_gateway=get_paystack_gateway(),
        transfer_service=get_transfer_service_manual(session),
    )
