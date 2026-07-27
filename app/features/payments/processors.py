import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import Depends
from app.core.models.notifications import NotificationType
from app.core.models.tasks import (
    PaymentStatus,
    Task,
)
from app.core.models.transactions import Transaction, TransactionStatus, TransactionType
from app.core.models.payments import PayoutQueue, PayoutStatus
from app.core.repository import GetRepository, Repository, QueryOptions
from app.features.notifications.schemas import CreateNotification
from app.features.notifications.services import (
    NotificationService,
    get_notification_service,
)
from app.features.payments.celery.tasks import (
    process_debt_settlement,
    process_provider_payout,
)
from app.core.services.logger_service import LoggerService, get_logger_service
from app.core.utils.timer import Timer

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
        system_logger: LoggerService,
        payout_repo: Repository[PayoutQueue],
    ):
        super().__init__(transaction_repo, notification_service, system_logger)
        self.task_repo = task_repo
        self.payout_repo = payout_repo

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

    async def _handle_charge_success(self, data: Dict[str, Any]) -> None:
        reference = data.get("reference")
        amount = data.get("amount", 0.0)
        metadata = data.get("metadata") or {}
        user_id = metadata.get("user_id")
        task_id = metadata.get("task_id")
        event_type = metadata.get("type")

        if not isinstance(reference, str):
            await self.system_logger.error("Missing required reference in charge.success payload", source="payments.webhook")
            return

        # 1. Handle Provider Debt Settlement Payment
        if event_type == "debt_settlement":
            provider_id = metadata.get("provider_id") or user_id
            if provider_id:
                await self.system_logger.info(
                    f"Triggering debt settlement task for provider {provider_id}, amount: ₦{amount:,.2f}", source="payments.webhook"
                )
                # pyrefly: ignore [not-callable]
                process_debt_settlement.delay(provider_id, amount, reference)
            return

        # 2. Handle Online Task Payment
        if event_type == "task_payment" and isinstance(task_id, str):
            await self.system_logger.info(
                f"Processing successful online payment for task {task_id}, reference: {reference}", source="payments.webhook"
            )
            task = await self.task_repo.get(task_id)
            if task:
                task.payment_status = PaymentStatus.PAID
                await self.task_repo.add(task)

                # Log successful task payment transaction
                transaction = Transaction(
                    amount=amount,
                    transaction_type=TransactionType.TASK_PAYMENT,
                    status=TransactionStatus.SUCCESS,
                    payment_mode="online",
                    user_id=user_id or task.customer_id,
                    task_id=task.id,
                    reference=reference,
                    metadata_info=data,
                )
                await self.transaction_repo.add(transaction)

                # Update PayoutQueue to CUSTOMER_PAID
                payouts = await self.payout_repo.get_all(QueryOptions(filters={"task_id": task.id}))
                if payouts:
                    for p in payouts:
                        await self.payout_repo.update(p.id, {"status": PayoutStatus.CUSTOMER_PAID})

                # Immediately trigger transfer out payout to provider via Celery task
                if task.assigned_provider_id and task.provider_payout:
                    await self.system_logger.info(
                        f"Immediately triggering provider payout for provider {task.assigned_provider_id} on task {task.id}", source="payments.webhook"
                    )
                    # pyrefly: ignore [not-callable]
                    process_provider_payout.delay(
                        task.id, task.assigned_provider_id, task.provider_payout
                    )

                if user_id:
                    await self.__dispatch_success_notification(
                        user_id, amount, reference, transaction.id
                    )
                return

        if isinstance(user_id, str):
            transaction = await self.__create_transaction(
                amount, TransactionStatus.SUCCESS, user_id, task_id, reference, data
            )
            await self.__dispatch_success_notification(
                user_id, amount, reference, transaction.id
            )

    async def _handle_charge_failed(self, data: Dict[str, Any]) -> None:
        reference = data.get("reference")
        amount = data.get("amount", 0.0)
        metadata = data.get("metadata") or {}
        user_id = metadata.get("user_id")
        task_id = metadata.get("task_id")

        if not isinstance(reference, str) or not isinstance(user_id, str):
            await self.system_logger.error(
                "Missing required fields (reference or user_id) in charge.failed payload", source="payments.webhook"
            )
            return

        await self.system_logger.info(f"Processing failed charge for reference: {reference}", source="payments.webhook")

        await self.__create_transaction(
            amount, TransactionStatus.FAILED, user_id, task_id, reference, data
        )
        await self.__dispatch_failure_notification(user_id, amount, reference)

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
            amount=amount,
            transaction_type=TransactionType.TASK_PAYMENT,
            status=status,
            user_id=user_id,
            task_id=task_id,
            reference=reference,
            metadata_info=data,
        )
        await self.transaction_repo.add(transaction)
        await self.system_logger.info(
            f"Created transaction for charge: {reference} with status: {status}", source="payments.webhook"
        )
        return transaction



    async def __dispatch_success_notification(
        self, user_id: str, amount: float, reference: str, transaction_id: str
    ) -> None:
        if not user_id:
            return

        await self.notification_service.notify(
            recepients=[user_id],
            title="Payment Successful",
            body=f"Your payment of {amount} has been received successfully.",
            type=NotificationType.PAYMENT_RECEIVED,
            data={"transaction_id": transaction_id, "reference": reference},
        )
        await self.system_logger.info(f"Dispatched payment success notification to user: {user_id}", source="payments.webhook")

    async def __dispatch_failure_notification(
        self, user_id: str, amount: float, reference: str
    ) -> None:
        if not user_id:
            return

        await self.notification_service.notify(
            recepients=[user_id],
            title="Payment Failed",
            body=f"Your payment of {amount} could not be processed. Please try again.",
            type=NotificationType.PAYMENT_FAILED,
            data={"reference": reference},
        )
        await self.system_logger.info(f"Dispatched payment failure notification to user: {user_id}", source="payments.webhook")


class TransferWebhookProcessor(WebhookProcessor):
    """Handles outgoing payouts/transfers."""

    def __init__(
        self,
        transaction_repo: Repository[Transaction],
        notification_service: NotificationService,
        system_logger: LoggerService,
        payout_repo: Repository[PayoutQueue],
    ):
        super().__init__(transaction_repo, notification_service, system_logger)
        self.payout_repo = payout_repo

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

    async def _handle_transfer_success(self, data: Dict[str, Any]) -> None:
        reference = data.get("reference")
        amount = data.get("amount", 0.0)
        metadata = data.get("metadata") or {}
        user_id = metadata.get("user_id")
        task_id = metadata.get("task_id")

        if not isinstance(reference, str) or not isinstance(user_id, str):
            await self.system_logger.error(
                "Missing required fields (reference or user_id) in transfer.success payload", source="payments.webhook"
            )
            return

        await self.system_logger.info(f"Processing successful transfer for reference: {reference}", source="payments.webhook")

        # Update PayoutQueue status
        payouts = await self.payout_repo.get_all(QueryOptions(filters={"reference": reference}))
        if payouts:
            for p in payouts:
                await self.payout_repo.update(p.id, {"status": PayoutStatus.COMPLETED})

        await self.__create_transaction(
            amount, TransactionStatus.SUCCESS, user_id, task_id, reference, data
        )
        await self.__dispatch_notification(
            user_id,
            NotificationType.PAYMENT_RECEIVED,
            "Payout Successful",
            f"Your payout of {amount} has been processed successfully.",
            reference,
        )

    async def _handle_transfer_failed(self, data: Dict[str, Any]) -> None:
        reference = data.get("reference")
        amount = data.get("amount", 0.0)
        metadata = data.get("metadata") or {}
        user_id = metadata.get("user_id")
        task_id = metadata.get("task_id")

        if not isinstance(reference, str) or not isinstance(user_id, str):
            await self.system_logger.error(
                "Missing required fields (reference or user_id) in transfer.failed payload", source="payments.webhook"
            )
            return

        await self.system_logger.info(f"Processing failed transfer for reference: {reference}", source="payments.webhook")

        # Update PayoutQueue status
        payouts = await self.payout_repo.get_all(QueryOptions(filters={"reference": reference}))
        if payouts:
            for p in payouts:
                await self.payout_repo.update(p.id, {"status": PayoutStatus.CANCELLED})

        await self.__create_transaction(
            amount, TransactionStatus.FAILED, user_id, task_id, reference, data
        )
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
            f"Created transaction for transfer: {reference} with status: {status}", source="payments.webhook"
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

        await self.notification_service.notify(
            recepients=[user_id],
            title=title,
            body=body,
            type=type,
            data={"reference": reference},
        )
        await self.system_logger.info(f"Dispatched payout notification ({type}) to user: {user_id}", source="payments.webhook")


def get_payment_processor(
    transaction_repo: Repository[Transaction] = Depends(GetRepository(Transaction)),
    notification_service: NotificationService = Depends(get_notification_service),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    system_logger: LoggerService = Depends(get_logger_service),
    payout_repo: Repository[PayoutQueue] = Depends(GetRepository(PayoutQueue)),
) -> PaymentWebhookProcessor:
    return PaymentWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
        task_repo=task_repo,
        system_logger=system_logger,
        payout_repo=payout_repo,
    )


def get_transfer_processor(
    transaction_repo: Repository[Transaction] = Depends(GetRepository(Transaction)),
    notification_service: NotificationService = Depends(get_notification_service),
    system_logger: LoggerService = Depends(get_logger_service),
    payout_repo: Repository[PayoutQueue] = Depends(GetRepository(PayoutQueue)),
) -> TransferWebhookProcessor:
    return TransferWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
        system_logger=system_logger,
        payout_repo=payout_repo,
    )
