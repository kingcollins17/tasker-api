import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from fastapi import Depends
from sqlmodel import col, select, update

from app.core.models.notifications import NotificationType
from app.core.models.payments import DebtReason, PayoutQueue, PayoutStatus, ProviderDebt
from app.core.models.tasks import PaymentStatus, Task
from app.core.models.transactions import Transaction, TransactionStatus, TransactionType
from app.core.models.transfers import Transfer, TransferStatus
from app.core.repository import GetRepository, QueryOptions, Repository
from app.core.services.logger_service import LoggerService, get_logger_service
from app.core.utils.timer import Timer
from app.features.notifications.services import (
    NotificationService,
    get_notification_service,
)
from app.features.payments.services import PaymentService, get_payment_service
from app.features.payments.transfer_service import TransferService, get_transfer_service

logger = logging.getLogger(__name__)


class WebhookProcessor(ABC):
    """Abstract base class for all webhook event processors."""

    def __init__(
        self,
        transaction_repo: Repository[Transaction],
        notification_service: NotificationService,
        system_logger: LoggerService,
    ):
        self.transaction_repo = transaction_repo
        self.notification_service = notification_service
        self.system_logger = system_logger

    @abstractmethod
    async def process(self, event: str, data: Dict[str, Any]) -> None:
        """Process the webhook payload data."""
        pass


class PaymentWebhookProcessor(WebhookProcessor):
    """Handles incoming payments (e.g. charge success)."""

    def __init__(
        self,
        transaction_repo: Repository[Transaction],
        notification_service: NotificationService,
        task_repo: Repository[Task],
        debt_repo: Repository[ProviderDebt],
        system_logger: LoggerService,
        payout_repo: Repository[PayoutQueue],
        transfer_service: TransferService,
        payment_service: PaymentService,
    ):
        super().__init__(transaction_repo, notification_service, system_logger)
        self.task_repo = task_repo
        self.payout_repo = payout_repo
        self.debt_repo = debt_repo
        self.transfer_service = transfer_service
        self.payment_service = payment_service

    async def process(self, event: str, data: Dict[str, Any]) -> None:
        try:
            timer = Timer()
            timer.start()
            if event == "charge.success":
                await self._handle_charge_success(data)
            elif event == "charge.failed":
                await self._handle_charge_failed(data)
            await self.system_logger.metric(
                f"Processed payment webhook: {event}",
                timer.stop(),
                source="payments.webhook",
            )
        except Exception as e:
            await self.system_logger.error(
                f"Error processing payment webhook ({event}): {str(e)}",
                source="payments.webhook",
                metadata={"data": data},
            )
            raise

    async def _handle_charge_success(self, data: Dict[str, Any]) -> None:
        reference = data.get("reference")
        if not isinstance(reference, str) or not reference.strip():
            await self.system_logger.error(
                "Missing or invalid required reference in charge.success payload",
                source="payments.webhook",
            )
            return

        try:
            amount = float(data.get("amount", 0.0))
        except (TypeError, ValueError):
            amount = 0.0

        metadata = data.get("metadata") or {}
        user_id = metadata.get("user_id")
        task_id = metadata.get("task_id")
        event_type = metadata.get("type")
        event_type = str(event_type) if event_type is not None else None
        is_task_payment = bool(event_type and event_type.lower() == "task_payment")
        is_debt_settlement = bool(event_type and event_type.lower() == "debt_settlement")

        # 1. Idempotency Check: check if success transaction with reference already processed
        stmt = select(Transaction).where(
            col(Transaction.reference) == reference,
            col(Transaction.status) == TransactionStatus.SUCCESS,
        )
        existing_tx_res = await self.transaction_repo.execute(stmt)
        if existing_tx_res.first():
            await self.system_logger.info(
                f"Charge success webhook for reference {reference} already processed. Skipping.",
                source="payments.webhook",
            )
            return

        # 2. If task payment, validate task existence and pricing before proceeding
        task: Optional[Task] = None
        if is_task_payment and task_id:
            task = await self.task_repo.get(task_id)
            if not task:
                await self.system_logger.error(
                    f"Task {task_id} referenced in charge.success payload not found.",
                    source="payments.webhook",
                )
                return

            if task.customer_total_price and task.customer_total_price > 0:
                if amount < task.customer_total_price:
                    await self.system_logger.error(
                        f"Payment amount ₦{amount:,.2f} is less than expected task price ₦{task.customer_total_price:,.2f} for task {task_id}.",
                        source="payments.webhook",
                    )
                    return

        # Create Transaction record
        transaction = await self.__create_transaction(
            amount=amount,
            transaction_type=(
                TransactionType.DEBT_SETTLEMENT
                if is_debt_settlement
                else TransactionType.TASK_PAYMENT
            ),
            status=TransactionStatus.SUCCESS,
            user_id=user_id,
            task_id=task_id,
            reference=reference,
            data=data,
        )

        # 3. Handle Provider Debt Settlement Payment
        if is_debt_settlement:
            provider_id = metadata.get("provider_id") or user_id
            if provider_id:
                await self.system_logger.info(
                    f"Recording debt settlement task for provider {provider_id}, amount: ₦{amount:,.2f}",
                    source="payments.webhook",
                )
                payment_entry = ProviderDebt(
                    provider_id=provider_id,
                    amount=-amount,
                    reason=DebtReason.DEBT_PAYMENT,
                    description=f"Online debt payment via reference {reference}",
                )
                await self.debt_repo.add(payment_entry)
            return

        # 4. Handle Online Task Payment
        if is_task_payment and task:
            await self.system_logger.info(
                f"Processing successful online payment for task {task.id}, reference: {reference}",
                source="payments.webhook",
            )

            # State Machine Check: Only transition if not already CUSTOMER_PAID or PAID
            allowed_statuses = [
                PaymentStatus.PENDING,
                PaymentStatus.PAYMENT_REQUESTED,
                PaymentStatus.FAILED,
            ]
            if task.payment_status in allowed_statuses:
                task.payment_status = PaymentStatus.CUSTOMER_PAID
                await self.task_repo.add(task)

                # Update PayoutQueue object status to customer paid
                await self.payout_repo.execute(
                    update(PayoutQueue)
                    .where(
                        col(PayoutQueue.task_id) == task.id,
                        col(PayoutQueue.provider_id) == task.assigned_provider_id,
                        col(PayoutQueue.status).in_([PayoutStatus.PENDING, PayoutStatus.CANCELLED]),
                    )
                    .values(status=PayoutStatus.CUSTOMER_PAID)
                )

                # Ensure task has an assigned provider, then dispatch flow to pay money to provider
                if task.assigned_provider_id:
                    await self.payment_service.process_provider_payout(
                        task_id=task.id, provider_id=task.assigned_provider_id
                    )
            else:
                await self.system_logger.info(
                    f"Task {task.id} payment status is already {task.payment_status}, skipping provider payout trigger.",
                    source="payments.webhook",
                )

            if user_id:
                await self.__dispatch_success_notification(
                    user_id,
                    amount,
                    reference,
                    transaction.id,
                )
            return

        # Default fallback for general non-task charge success
        if isinstance(user_id, str):
            await self.__dispatch_success_notification(
                user_id,
                amount,
                reference,
                transaction.id,
            )

    async def _handle_charge_failed(self, data: Dict[str, Any]) -> None:
        reference = data.get("reference")
        try:
            amount = float(data.get("amount", 0.0))
        except (TypeError, ValueError):
            amount = 0.0

        metadata = data.get("metadata") or {}
        user_id = metadata.get("user_id")
        task_id = metadata.get("task_id")

        if not isinstance(reference, str) or not isinstance(user_id, str):
            await self.system_logger.error(
                "Missing required fields (reference or user_id) in charge.failed payload",
                source="payments.webhook",
            )
            return

        # Idempotency check for failed charges
        stmt = select(Transaction).where(
            col(Transaction.reference) == reference,
            col(Transaction.status) == TransactionStatus.FAILED,
        )
        existing_tx_res = await self.transaction_repo.execute(stmt)
        if existing_tx_res.first():
            await self.system_logger.info(
                f"Charge failed webhook for reference {reference} already processed. Skipping.",
                source="payments.webhook",
            )
            return

        await self.system_logger.info(
            f"Processing failed charge for reference: {reference}",
            source="payments.webhook",
        )

        await self.__create_transaction(
            amount,
            TransactionStatus.FAILED,
            user_id,
            task_id,
            reference,
            data,
        )
        await self.__dispatch_failure_notification(user_id, amount, reference)

    async def __create_transaction(
        self,
        amount: float,
        status: TransactionStatus,
        user_id: Optional[str],
        task_id: Optional[str],
        reference: str,
        data: Dict[str, Any],
        transaction_type: TransactionType = TransactionType.TASK_PAYMENT,
    ) -> Transaction:
        transaction = Transaction(
            amount=amount,
            transaction_type=transaction_type,
            status=status,
            user_id=user_id,
            task_id=task_id,
            reference=reference,
            payment_mode="online",
            metadata_info=data,
        )
        await self.transaction_repo.add(transaction)
        await self.system_logger.info(
            f"Created transaction for charge: {reference} with status: {status}",
            source="payments.webhook",
        )
        return transaction

    async def __dispatch_success_notification(
        self, user_id: str, amount: float, reference: str, transaction_id: str
    ) -> None:
        if not user_id:
            return

        try:
            await self.notification_service.notify(
                recepients=[user_id],
                title="Payment Successful",
                body=f"Your payment of {amount} has been received successfully.",
                type=NotificationType.PAYMENT_RECEIVED,
                data={"transaction_id": transaction_id, "reference": reference},
            )
            await self.system_logger.info(
                f"Dispatched payment success notification to user: {user_id}",
                source="payments.webhook",
            )
        except Exception as e:
            await self.system_logger.error(
                f"Failed to dispatch payment success notification to user {user_id}: {str(e)}",
                source="payments.webhook",
            )

    async def __dispatch_failure_notification(
        self, user_id: str, amount: float, reference: str
    ) -> None:
        if not user_id:
            return

        try:
            await self.notification_service.notify(
                recepients=[user_id],
                title="Payment Failed",
                body=f"Your payment of {amount} could not be processed. Please try again.",
                type=NotificationType.PAYMENT_FAILED,
                data={"reference": reference},
            )
            await self.system_logger.info(
                f"Dispatched payment failure notification to user: {user_id}",
                source="payments.webhook",
            )
        except Exception as e:
            await self.system_logger.error(
                f"Failed to dispatch payment failure notification to user {user_id}: {str(e)}",
                source="payments.webhook",
            )


class TransferWebhookProcessor(WebhookProcessor):
    """Handles outgoing payouts/transfers."""

    def __init__(
        self,
        transaction_repo: Repository[Transaction],
        notification_service: NotificationService,
        system_logger: LoggerService,
        payout_repo: Repository[PayoutQueue],
        task_repo: Repository[Task],
        transfer_service: TransferService,
    ):
        super().__init__(transaction_repo, notification_service, system_logger)
        self.payout_repo = payout_repo
        self.task_repo = task_repo
        self.transfer_service = transfer_service

    async def process(self, event: str, data: Dict[str, Any]) -> None:
        try:
            timer = Timer()
            timer.start()
            if event == "transfer.success":
                await self._handle_transfer_success(data)
            elif event == "transfer.failed":
                await self._handle_transfer_failed(data)
            await self.system_logger.metric(
                f"Processed transfer webhook: {event}",
                timer.stop(),
                source="payments.webhook",
            )
        except Exception as e:
            await self.system_logger.error(
                f"Error processing transfer webhook ({event}): {str(e)}",
                source="payments.webhook",
                metadata={"data": data},
            )
            raise

    async def _handle_transfer_success(self, data: Dict[str, Any]) -> None:
        reference = data.get("reference")
        try:
            amount = float(data.get("amount", 0.0))
        except (TypeError, ValueError):
            amount = 0.0

        metadata = data.get("metadata") or {}
        user_id = metadata.get("user_id")
        task_id = metadata.get("task_id")

        if not isinstance(reference, str) or not isinstance(user_id, str):
            await self.system_logger.error(
                "Missing required fields (reference or user_id) in transfer.success payload",
                source="payments.webhook",
            )
            return

        # Idempotency check
        stmt = select(Transaction).where(
            col(Transaction.reference) == reference,
            col(Transaction.status) == TransactionStatus.SUCCESS,
        )
        existing_tx_res = await self.transaction_repo.execute(stmt)
        if existing_tx_res.first():
            await self.system_logger.info(
                f"Transfer success for reference {reference} already processed. Skipping.",
                source="payments.webhook",
            )
            return

        await self.system_logger.info(
            f"Processing successful transfer for reference: {reference}",
            source="payments.webhook",
        )

        # 1. Update Transfer record to COMPLETED via TransferService state machine
        transfer_stmt = select(Transfer).where(
            (col(Transfer.provider_transfer_id) == reference)
            | (col(Transfer.idempotency_key) == reference)
        )
        if task_id:
            transfer_stmt = select(Transfer).where(
                (col(Transfer.provider_transfer_id) == reference)
                | (col(Transfer.idempotency_key) == reference)
                | (col(Transfer.task_id) == task_id)
            )
        transfer_res = await self.transfer_service.transfer_repo.execute(transfer_stmt)
        transfer = transfer_res.first()

        if transfer and transfer.status != TransferStatus.COMPLETED:
            await self.transfer_service._mark_completed(
                transfer, provider_transfer_id=reference
            )
        else:
            # Fallback for updating PayoutQueue & Task if Transfer record was not found or already processed
            payouts = await self.payout_repo.get_all(
                QueryOptions(filters={"reference": reference})
            )
            if payouts:
                for p in payouts:
                    if p.status in [PayoutStatus.PENDING, PayoutStatus.CUSTOMER_PAID, PayoutStatus.TRANSFER_INITIATED]:
                        await self.payout_repo.update(p.id, {"status": PayoutStatus.COMPLETED})

            if task_id:
                task = await self.task_repo.get(task_id)
                if task and task.payment_status != PaymentStatus.PAID:
                    task.payment_status = PaymentStatus.PAID
                    await self.task_repo.add(task)

        # 2. Record Transaction entry
        await self.__create_transaction(
            amount, TransactionStatus.SUCCESS, user_id, task_id, reference, data
        )
        # 3. Dispatch user notification
        await self.__dispatch_notification(
            user_id,
            NotificationType.PAYMENT_RECEIVED,
            "Payout Successful",
            f"Your payout of {amount} has been processed successfully.",
            reference,
        )

    async def _handle_transfer_failed(self, data: Dict[str, Any]) -> None:
        reference = data.get("reference")
        try:
            amount = float(data.get("amount", 0.0))
        except (TypeError, ValueError):
            amount = 0.0

        metadata = data.get("metadata") or {}
        user_id = metadata.get("user_id")
        task_id = metadata.get("task_id")

        if not isinstance(reference, str) or not isinstance(user_id, str):
            await self.system_logger.error(
                "Missing required fields (reference or user_id) in transfer.failed payload",
                source="payments.webhook",
            )
            return

        # Idempotency check
        stmt = select(Transaction).where(
            col(Transaction.reference) == reference,
            col(Transaction.status) == TransactionStatus.FAILED,
        )
        existing_tx_res = await self.transaction_repo.execute(stmt)
        if existing_tx_res.first():
            await self.system_logger.info(
                f"Transfer failed for reference {reference} already processed. Skipping.",
                source="payments.webhook",
            )
            return

        await self.system_logger.info(
            f"Processing failed transfer for reference: {reference}",
            source="payments.webhook",
        )

        # 1. Update Transfer record to FAILED via TransferService state machine
        transfer_stmt = select(Transfer).where(
            (col(Transfer.provider_transfer_id) == reference)
            | (col(Transfer.idempotency_key) == reference)
        )
        if task_id:
            transfer_stmt = select(Transfer).where(
                (col(Transfer.provider_transfer_id) == reference)
                | (col(Transfer.idempotency_key) == reference)
                | (col(Transfer.task_id) == task_id)
            )
        transfer_res = await self.transfer_service.transfer_repo.execute(transfer_stmt)
        transfer = transfer_res.first()

        if transfer and transfer.status != TransferStatus.FAILED:
            reason = data.get("reason") or "Transfer failed via webhook notification"
            await self.transfer_service._mark_failed(
                transfer, code="WEBHOOK_FAILED", reason=reason
            )
        else:
            # Fallback for updating PayoutQueue & Task if Transfer record was not found or already processed
            payouts = await self.payout_repo.get_all(
                QueryOptions(filters={"reference": reference})
            )
            if payouts:
                for p in payouts:
                    if p.status != PayoutStatus.COMPLETED:
                        await self.payout_repo.update(p.id, {"status": PayoutStatus.CANCELLED})

            if task_id:
                task = await self.task_repo.get(task_id)
                if task and task.payment_status != PaymentStatus.PAID:
                    task.payment_status = PaymentStatus.FAILED
                    await self.task_repo.add(task)

        # 2. Record Transaction entry
        await self.__create_transaction(
            amount, TransactionStatus.FAILED, user_id, task_id, reference, data
        )
        # 3. Dispatch user notification
        await self.__dispatch_notification(
            user_id,
            NotificationType.PAYMENT_FAILED,
            "Payout Failed",
            "There was an issue processing your payout. Please check your details.",
            reference,
        )

    async def __create_transaction(
        self,
        amount: float,
        status: TransactionStatus,
        user_id: str,
        task_id: Optional[str],
        reference: str,
        data: Dict[str, Any],
    ) -> Transaction:
        transaction = Transaction(
            amount=-abs(amount),
            transaction_type=TransactionType.PROVIDER_PAYOUT,
            status=status,
            user_id=user_id,
            task_id=task_id,
            reference=reference,
            metadata_info=data,
        )
        await self.transaction_repo.add(transaction)
        await self.system_logger.info(
            f"Created transaction for transfer: {reference} with status: {status}",
            source="payments.webhook",
        )
        return transaction

    async def __dispatch_notification(
        self,
        user_id: str,
        type: NotificationType,
        title: str,
        body: str,
        reference: str,
    ) -> None:
        if not user_id:
            return

        try:
            await self.notification_service.notify(
                recepients=[user_id],
                title=title,
                body=body,
                type=type,
                data={"reference": reference},
            )
            await self.system_logger.info(
                f"Dispatched payout notification ({type}) to user: {user_id}",
                source="payments.webhook",
            )
        except Exception as e:
            await self.system_logger.error(
                f"Failed to dispatch payout notification ({type}) to user {user_id}: {str(e)}",
                source="payments.webhook",
            )


def get_payment_processor(
    transaction_repo: Repository[Transaction] = Depends(GetRepository(Transaction)),
    notification_service: NotificationService = Depends(get_notification_service),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    system_logger: LoggerService = Depends(get_logger_service),
    payout_repo: Repository[PayoutQueue] = Depends(GetRepository(PayoutQueue)),
    transfer_service: TransferService = Depends(get_transfer_service),
    debt_repo: Repository[ProviderDebt] = Depends(GetRepository(ProviderDebt)),
    payment_service: PaymentService = Depends(get_payment_service),
) -> PaymentWebhookProcessor:
    return PaymentWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
        task_repo=task_repo,
        system_logger=system_logger,
        payout_repo=payout_repo,
        transfer_service=transfer_service,
        debt_repo=debt_repo,
        payment_service=payment_service,
    )


def get_transfer_processor(
    transaction_repo: Repository[Transaction] = Depends(GetRepository(Transaction)),
    notification_service: NotificationService = Depends(get_notification_service),
    system_logger: LoggerService = Depends(get_logger_service),
    payout_repo: Repository[PayoutQueue] = Depends(GetRepository(PayoutQueue)),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    transfer_service: TransferService = Depends(get_transfer_service),
) -> TransferWebhookProcessor:
    return TransferWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
        system_logger=system_logger,
        payout_repo=payout_repo,
        task_repo=task_repo,
        transfer_service=transfer_service,
    )
