import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from datetime import datetime

from fastapi import Depends
from app.core.models.notifications import NotificationType
from app.core.models.transactions import Transaction, TransactionStatus, TransactionType
from app.core.models.tasks import TaskAssignment, Task, TaskBid, TaskStatus, TaskBidStatus
from app.core.repository import Repository, GetRepository
from app.features.notifications.schemas import CreateNotification
from app.features.notifications.services import NotificationService, get_notification_service

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
        bid_repo: Repository[TaskBid],
    ):
        super().__init__(transaction_repo, notification_service)
        self.task_assignment_repo = task_assignment_repo
        self.task_repo = task_repo
        self.bid_repo = bid_repo

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

        if not isinstance(reference, str) or not isinstance(user_id, str):
            logger.error("Missing required fields (reference or user_id) in charge.success payload")
            return

        logger.info(f"Processing successful charge for reference: {reference}")

        transaction = await self.__create_transaction(amount, TransactionStatus.SUCCESS, user_id, task_id, reference, data)
        
        if isinstance(task_id, str):
            await self.__create_task_assignment(metadata, task_id)
        
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
        bid_id = metadata.get("bid_id")
        if not bid_id or not task_id:
            return

        bid = await self.bid_repo.get(bid_id)
        if not bid:
            return

        assignment = TaskAssignment(
            task_id=task_id,
            provider_id=bid.provider_id,
            accepted_bid_id=bid_id,
            accepted_price=bid.price,
        )
        await self.task_assignment_repo.add(assignment)
        
        await self.task_repo.update(task_id, {"status": TaskStatus.ASSIGNED})
        await self.bid_repo.update(bid_id, {"status": TaskBidStatus.ACCEPTED})
        logger.info(f"Created assignment for task {task_id} with bid {bid_id}")

        task = await self.task_repo.get(task_id)
        if task:
            await self.__dispatch_provider_booked_notification(bid.provider_id, task)

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
    bid_repo: Repository[TaskBid] = Depends(GetRepository(TaskBid)),
) -> PaymentWebhookProcessor:
    return PaymentWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
        task_assignment_repo=task_assignment_repo,
        task_repo=task_repo,
        bid_repo=bid_repo,
    )


def get_transfer_processor(
    transaction_repo: Repository[Transaction] = Depends(GetRepository(Transaction)),
    notification_service: NotificationService = Depends(get_notification_service),
) -> TransferWebhookProcessor:
    return TransferWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
    )
