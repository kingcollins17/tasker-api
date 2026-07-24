import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import Depends
from app.core.models.notifications import NotificationType
from app.core.models.tasks import (
    DispatchAttemptStatus,
    PaymentStatus,
    Task,
    TaskAssignment,
    TaskDispatchAttempt,
    TaskStatus,
)
from app.core.models.transactions import Transaction, TransactionStatus, TransactionType
from app.core.repository import GetRepository, Repository
from app.features.notifications.schemas import CreateNotification
from app.features.notifications.services import NotificationService, get_notification_service
from app.features.payments.celery.tasks import (
    process_debt_settlement,
    process_provider_payout,
)

logger = logging.getLogger(__name__)


class WebhookProcessor(ABC):
    """Abstract base class for all webhook event processors."""

    def __init__(
        self,
        transaction_repo: Repository[Transaction],
        notification_service: NotificationService,
    ):
        self.transaction_repo = transaction_repo
        self.notification_service = notification_service

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
        task_assignment_repo: Repository[TaskAssignment],
        task_repo: Repository[Task],
        attempt_repo: Repository[TaskDispatchAttempt],
    ):
        super().__init__(transaction_repo, notification_service)
        self.task_assignment_repo = task_assignment_repo
        self.task_repo = task_repo
        self.attempt_repo = attempt_repo

    async def process(self, event: str, data: Dict[str, Any]) -> None:
        if event == "charge.success":
            await self._handle_charge_success(data)
        elif event == "charge.failed":
            await self._handle_charge_failed(data)

    async def _handle_charge_success(self, data: Dict[str, Any]) -> None:
        reference = data.get("reference")
        amount = data.get("amount", 0.0)
        metadata = data.get("metadata") or {}
        user_id = metadata.get("user_id")
        task_id = metadata.get("task_id")
        event_type = metadata.get("type")

        if not isinstance(reference, str):
            logger.error("Missing required reference in charge.success payload")
            return

        # 1. Handle Provider Debt Settlement Payment
        if event_type == "debt_settlement":
            provider_id = metadata.get("provider_id") or user_id
            if provider_id:
                logger.info(
                    f"Triggering debt settlement task for provider {provider_id}, amount: ₦{amount:,.2f}"
                )
                # pyrefly: ignore [not-callable]
                process_debt_settlement.delay(provider_id, amount, reference)
            return

        # 2. Handle Online Task Payment
        if isinstance(task_id, str):
            logger.info(f"Processing successful online payment for task {task_id}, reference: {reference}")
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

                # Immediately trigger transfer out payout to provider via Celery task
                if task.assigned_provider_id and task.provider_payout:
                    logger.info(
                        f"Immediately triggering provider payout for provider {task.assigned_provider_id} on task {task.id}"
                    )
                    # pyrefly: ignore [not-callable]
                    process_provider_payout.delay(
                        task.id, task.assigned_provider_id, task.provider_payout
                    )

                if user_id:
                    await self.__dispatch_success_notification(user_id, amount, reference, transaction.id)
                return

        if isinstance(user_id, str):
            transaction = await self.__create_transaction(amount, TransactionStatus.SUCCESS, user_id, task_id, reference, data)
            await self.__dispatch_success_notification(user_id, amount, reference, transaction.id)

    async def _handle_charge_failed(self, data: Dict[str, Any]) -> None:
        reference = data.get("reference")
        amount = data.get("amount", 0.0)
        metadata = data.get("metadata") or {}
        user_id = metadata.get("user_id")
        task_id = metadata.get("task_id")

        if not isinstance(reference, str) or not isinstance(user_id, str):
            logger.error("Missing required fields (reference or user_id) in charge.failed payload")
            return

        logger.info(f"Processing failed charge for reference: {reference}")

        await self.__create_transaction(amount, TransactionStatus.FAILED, user_id, task_id, reference, data)
        await self.__dispatch_failure_notification(user_id, amount, reference)

    async def __create_transaction(
        self, amount: float, status: TransactionStatus, user_id: str, task_id: Optional[str], reference: str, data: Dict[str, Any]
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
        logger.info(f"Created transaction for charge: {reference} with status: {status}")
        return transaction

    async def __create_task_assignment(self, metadata: Dict[str, Any], task_id: str) -> None:
        attempt_id = metadata.get("attempt_id") or metadata.get("dispatch_attempt_id")
        if not attempt_id or not task_id:
            return

        attempt = await self.attempt_repo.get(attempt_id)
        if not attempt:
            return

        assignment = TaskAssignment(
            task_id=task_id,
            provider_id=attempt.provider_id,
            accepted_dispatch_attempt_id=attempt_id,
            accepted_price=attempt.offered_payout,
        )
        await self.task_assignment_repo.add(assignment)
        
        await self.task_repo.update(task_id, {"status": TaskStatus.ASSIGNED})
        await self.attempt_repo.update(attempt_id, {"status": DispatchAttemptStatus.ACCEPTED})
        logger.info(f"Created assignment for task {task_id} with attempt {attempt_id}")

        task = await self.task_repo.get(task_id)
        if task:
            await self.__dispatch_provider_booked_notification(attempt.provider_id, task)

    async def __dispatch_provider_booked_notification(self, provider_id: str, task: Task) -> None:
        if not provider_id:
            return

        body = f"You have been booked for the task '{task.title}'."
        if task.scheduled_start_at:
            time_str = self.__format_scheduled_time(task.scheduled_start_at)
            body = f"You have been booked for the task '{task.title}' scheduled for {time_str}."

        schema = CreateNotification(
            type=NotificationType.TASK_ACCEPTED,
            title="Task Booked",
            body=body,
            recipient_ids=[provider_id],
            data={"task_id": task.id},
        )
        await self.notification_service.create_notification(schema)
        logger.info(f"Dispatched provider booked notification to user: {provider_id}")

    def __format_scheduled_time(self, dt: datetime) -> str:
        now = datetime.now()
        time_str = dt.strftime("%I:%M %p").lstrip('0').replace(':00', '')
        if dt.date() == now.date():
            return f"{time_str} today"
        elif (dt.date() - now.date()).days == 1:
            return f"{time_str} tomorrow"
        else:
            day = dt.day
            if 4 <= day <= 20 or 24 <= day <= 30:
                suffix = "th"
            else:
                suffix = ["st", "nd", "rd"][day % 10 - 1]
            return f"{day}{suffix} {dt.strftime('%b')}"

    async def __dispatch_success_notification(
        self, user_id: str, amount: float, reference: str, transaction_id: str
    ) -> None:
        if not user_id:
            return
            
        schema = CreateNotification(
            type=NotificationType.PAYMENT_RECEIVED,
            title="Payment Successful",
            body=f"Your payment of {amount} has been received successfully.",
            recipient_ids=[user_id],
            data={"transaction_id": transaction_id, "reference": reference},
        )
        await self.notification_service.create_notification(schema)
        logger.info(f"Dispatched payment success notification to user: {user_id}")

    async def __dispatch_failure_notification(
        self, user_id: str, amount: float, reference: str
    ) -> None:
        if not user_id:
            return
            
        schema = CreateNotification(
            type=NotificationType.PAYMENT_FAILED,
            title="Payment Failed",
            body=f"Your payment of {amount} could not be processed. Please try again.",
            recipient_ids=[user_id],
            data={"reference": reference},
        )
        await self.notification_service.create_notification(schema)
        logger.info(f"Dispatched payment failure notification to user: {user_id}")


class TransferWebhookProcessor(WebhookProcessor):
    """Handles outgoing payouts/transfers."""

    async def process(self, event: str, data: Dict[str, Any]) -> None:
        if event == "transfer.success":
            await self._handle_transfer_success(data)
        elif event == "transfer.failed":
            await self._handle_transfer_failed(data)

    async def _handle_transfer_success(self, data: Dict[str, Any]) -> None:
        reference = data.get("reference")
        amount = data.get("amount", 0.0)
        metadata = data.get("metadata") or {}
        user_id = metadata.get("user_id")
        task_id = metadata.get("task_id")

        if not isinstance(reference, str) or not isinstance(user_id, str):
            logger.error("Missing required fields (reference or user_id) in transfer.success payload")
            return
        
        logger.info(f"Processing successful transfer for reference: {reference}")
        
        await self.__create_transaction(amount, TransactionStatus.SUCCESS, user_id, task_id, reference, data)
        await self.__dispatch_notification(
                user_id,
                NotificationType.PAYMENT_RECEIVED,
                "Payout Successful",
                f"Your payout of {amount} has been processed successfully.",
                reference
            )

    async def _handle_transfer_failed(self, data: Dict[str, Any]) -> None:
        reference = data.get("reference")
        amount = data.get("amount", 0.0)
        metadata = data.get("metadata") or {}
        user_id = metadata.get("user_id")
        task_id = metadata.get("task_id")
        
        if not isinstance(reference, str) or not isinstance(user_id, str):
            logger.error("Missing required fields (reference or user_id) in transfer.failed payload")
            return

        logger.info(f"Processing failed transfer for reference: {reference}")
        
        await self.__create_transaction(amount, TransactionStatus.FAILED, user_id, task_id, reference, data)
        await self.__dispatch_notification(
                user_id,
                NotificationType.PAYMENT_FAILED,
                "Payout Failed",
                "There was an issue processing your payout. Please check your details.",
                reference
            )

    async def __create_transaction(
        self, amount: float, status: TransactionStatus, user_id: str, task_id: Optional[str], reference: str, data: Dict[str, Any]
    ) -> Transaction:
        transaction = Transaction(
            amount=-abs(float(amount)),
            transaction_type=TransactionType.PROVIDER_PAYOUT,
            status=status,
            user_id=user_id,
            task_id=task_id,
            reference=reference,
            metadata_info=data,
        )
        await self.transaction_repo.add(transaction)
        logger.info(f"Created transaction for transfer: {reference} with status: {status}")
        return transaction

    async def __dispatch_notification(
        self, user_id: str, type: NotificationType, title: str, body: str, reference: str
    ) -> None:
        if not user_id:
            return

        schema = CreateNotification(
            type=type,
            title=title,
            body=body,
            recipient_ids=[user_id],
            data={"reference": reference},
        )
        await self.notification_service.create_notification(schema)
        logger.info(f"Dispatched payout notification ({type}) to user: {user_id}")


def get_payment_processor(
    transaction_repo: Repository[Transaction] = Depends(GetRepository(Transaction)),
    notification_service: NotificationService = Depends(get_notification_service),
    task_assignment_repo: Repository[TaskAssignment] = Depends(GetRepository(TaskAssignment)),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    attempt_repo: Repository[TaskDispatchAttempt] = Depends(GetRepository(TaskDispatchAttempt)),
) -> PaymentWebhookProcessor:
    return PaymentWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
        task_assignment_repo=task_assignment_repo,
        task_repo=task_repo,
        attempt_repo=attempt_repo,
    )


def get_transfer_processor(
    transaction_repo: Repository[Transaction] = Depends(GetRepository(Transaction)),
    notification_service: NotificationService = Depends(get_notification_service),
) -> TransferWebhookProcessor:
    return TransferWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
    )
