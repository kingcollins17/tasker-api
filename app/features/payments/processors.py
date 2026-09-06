import logging
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
from app.core.utils.datetime_helper import lagos_now
from app.core.utils.timer import Timer
from app.features.notifications.services import (
    NotificationService,
    get_notification_service,
)
from app.features.payments.services import PaymentService, get_payment_service
from app.features.payments.transfer_service import TransferService, get_transfer_service

logger = logging.getLogger(__name__)


class PaymentWebhookProcessor:
    """Processor responsible for handling inbound payment webhooks (charge success/failed)."""

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
        self.transaction_repo = transaction_repo
        self.notification_service = notification_service
        self.task_repo = task_repo
        self.debt_repo = debt_repo
        self.system_logger = system_logger
        self.payout_repo = payout_repo
        self.transfer_service = transfer_service
        self.payment_service = payment_service

    async def process(
        self,
        event: str,
        *,
        reference: str,
        amount: float,
        user_id: Optional[str] = None,
        task_id: Optional[str] = None,
        event_type: Optional[str] = None,
        provider_id: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Route incoming payment event to the corresponding handler method."""
        try:
            timer = Timer()
            timer.start()

            # Delegate to specific charge event handlers
            if event == "charge.success":
                await self.handle_charge_success(
                    reference=reference,
                    amount=amount,
                    user_id=user_id,
                    task_id=task_id,
                    event_type=event_type,
                    provider_id=provider_id,
                    raw_data=raw_data,
                )
            elif event == "charge.failed":
                await self.handle_charge_failed(
                    reference=reference,
                    amount=amount,
                    user_id=user_id,
                    task_id=task_id,
                    raw_data=raw_data,
                )

            # Record system metric for processing execution time
            await self.system_logger.metric(
                f"Processed payment webhook: {event}",
                timer.stop(),
                source="payments.webhook",
            )
        except Exception as e:
            # Log error details and re-raise for upstream exception handling
            await self.system_logger.error(
                f"Error processing payment webhook ({event}): {str(e)}",
                source="payments.webhook",
                metadata={"reference": reference, "event": event},
            )
            raise

    async def handle_charge_success(
        self,
        *,
        reference: str,
        amount: float,
        user_id: Optional[str] = None,
        task_id: Optional[str] = None,
        event_type: Optional[str] = None,
        provider_id: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Handle successful charge payments (task payments or provider debt settlements)."""
        print(
            f"[DEBUG handle_charge_success] START - reference={reference}, amount={amount}, "
            f"user_id={user_id}, task_id={task_id}, event_type={event_type}, provider_id={provider_id}"
        )
        # Validate that a non-empty transaction reference is provided
        if not reference or not reference.strip():
            print(f"[DEBUG handle_charge_success] Missing or invalid reference: {reference}")
            await self.system_logger.error(
                "Missing or invalid required reference in charge.success payload",
                source="payments.webhook",
            )
            return

        is_task_payment = bool(event_type and event_type.lower() == "task_payment")
        is_debt_settlement = bool(
            event_type and event_type.lower() == "debt_settlement"
        )
        print(f"[DEBUG handle_charge_success] Event type check: is_task_payment={is_task_payment}, is_debt_settlement={is_debt_settlement}")

        # Optimistic Concurrency Control: acquire lock on PayoutQueue (skip if debt_settlement)
        if not is_debt_settlement and task_id:
            payout_stmt = select(PayoutQueue).where(col(PayoutQueue.task_id) == task_id)
            payout_res = await self.payout_repo.execute(payout_stmt)
            payout = payout_res.first()
            if payout:
                prev_version = payout.lock_version
                lock_stmt = (
                    update(PayoutQueue)
                    .where(
                        col(PayoutQueue.id) == payout.id,
                        col(PayoutQueue.lock_version) == prev_version,
                    )
                    .values(
                        lock_version=prev_version + 1,
                        updated_at=lagos_now(),
                    )
                )
                lock_res = await self.payout_repo.execute(lock_stmt)
                if lock_res.rowcount == 0:
                    print(f"[DEBUG handle_charge_success] Optimistic lock on PayoutQueue {payout.id} (version {prev_version}) failed. Skipping.")
                    await self.system_logger.info(
                        f"Optimistic lock on PayoutQueue {payout.id} (version {prev_version}) could not be acquired. Another execution has the lock. Skipping.",
                        source="payments.webhook",
                    )
                    return
                payout.lock_version = prev_version + 1
                print(f"[DEBUG handle_charge_success] Acquired optimistic lock on PayoutQueue {payout.id} (new version: {payout.lock_version})")

        # 1. Idempotency Check: verify if a successful transaction with this reference exists
        stmt = select(Transaction).where(
            col(Transaction.reference) == reference,
            col(Transaction.status) == TransactionStatus.SUCCESS,
        )
        existing_tx_res = await self.transaction_repo.execute(stmt)
        if existing_tx_res.first():
            print(f"[DEBUG handle_charge_success] Idempotency check hit: reference {reference} already processed. Skipping.")
            await self.system_logger.info(
                f"Charge success webhook for reference {reference} already processed. Skipping.",
                source="payments.webhook",
            )
            return

        # 2. Validate task existence and price if this is a task payment
        task: Optional[Task] = None
        if is_task_payment and task_id:
            task = await self.task_repo.get(task_id)
            print(f"[DEBUG handle_charge_success] Fetched task: {task.id if task else None}")
            if not task:
                print(f"[DEBUG handle_charge_success] Task {task_id} not found. Skipping.")
                await self.system_logger.error(
                    f"Task {task_id} referenced in charge.success payload not found.",
                    source="payments.webhook",
                )
                return

            # Verify that paid amount satisfies the expected task price
            if task.customer_total_price and task.customer_total_price > 0:
                print(f"[DEBUG handle_charge_success] Validating amount ₦{amount:,.2f} vs task.customer_total_price ₦{task.customer_total_price:,.2f}")
                if amount < task.customer_total_price:
                    print(f"[DEBUG handle_charge_success] Insufficient payment amount: ₦{amount:,.2f} < ₦{task.customer_total_price:,.2f}. Skipping.")
                    await self.system_logger.error(
                        f"Payment amount ₦{amount:,.2f} is less than expected task price ₦{task.customer_total_price:,.2f} for task {task_id}.",
                        source="payments.webhook",
                    )
                    return

        # 3. Create immutable audit Transaction record
        tx_type = (
            TransactionType.DEBT_SETTLEMENT
            if is_debt_settlement
            else TransactionType.TASK_PAYMENT
        )
        transaction = Transaction(
            amount=amount,
            transaction_type=tx_type,
            status=TransactionStatus.SUCCESS,
            user_id=user_id,
            task_id=task_id,
            reference=reference,
            payment_mode="online",
            metadata_info=raw_data or {},
        )
        await self.transaction_repo.add(transaction)
        print(f"[DEBUG handle_charge_success] Created transaction {transaction.id} with status SUCCESS for reference {reference}")
        await self.system_logger.info(
            f"Created transaction for charge: {reference} with status: {TransactionStatus.SUCCESS}",
            source="payments.webhook",
        )

        # 4. Handle Provider Debt Settlement payment recording
        if is_debt_settlement:
            debt_provider_id = provider_id or user_id
            print(f"[DEBUG handle_charge_success] Handling debt settlement for provider {debt_provider_id}, amount: ₦{amount:,.2f}")
            if debt_provider_id:
                await self.system_logger.info(
                    f"Recording debt settlement task for provider {debt_provider_id}, amount: ₦{amount:,.2f}",
                    source="payments.webhook",
                )
                payment_entry = ProviderDebt(
                    provider_id=debt_provider_id,
                    amount=-amount,
                    reason=DebtReason.DEBT_PAYMENT,
                    description=f"Online debt payment via reference {reference}",
                )
                await self.debt_repo.add(payment_entry)
                print(f"[DEBUG handle_charge_success] Recorded ProviderDebt settlement entry for provider {debt_provider_id}")
            print(f"[DEBUG handle_charge_success] END - Debt settlement complete")
            return

        # 5. Handle Online Task Payment state transitions
        if is_task_payment and task:
            print(f"[DEBUG handle_charge_success] Handling online task payment transitions for task {task.id}, current payment_status={task.payment_status}")
            await self.system_logger.info(
                f"Processing successful online payment for task {task.id}, reference: {reference}",
                source="payments.webhook",
            )

            # Update task state to CUSTOMER_PAID if currently in a processable state
            allowed_statuses = [
                PaymentStatus.PENDING,
                PaymentStatus.PAYMENT_REQUESTED,
                PaymentStatus.FAILED,
            ]
            print(f"[DEBUG handle_charge_success] Task payment_status = ({task.payment_status})")
            if task.payment_status is None or task.payment_status in allowed_statuses:
                print(f"[DEBUG handle_charge_success] Updating task payment status to CUSTOMER_PAID")
                task.payment_status = PaymentStatus.CUSTOMER_PAID
                await self.task_repo.add(task)

                # Update PayoutQueue status to CUSTOMER_PAID
                print(f"[DEBUG handle_charge_success] Updating PayoutQueue status to CUSTOMER_PAID for task {task.id}")
                await self.payout_repo.execute(
                    update(PayoutQueue)
                    .where(
                        col(PayoutQueue.task_id) == task.id,
                        col(PayoutQueue.provider_id) == task.assigned_provider_id,
                        col(PayoutQueue.status).in_(
                            [PayoutStatus.PENDING, PayoutStatus.CANCELLED]
                        ),
                    )
                    .values(status=PayoutStatus.CUSTOMER_PAID)
                )

                # Dispatch provider payout trigger if an assigned provider exists
                provider_id = task.assigned_provider_id or (task.assignment.provider_id if task.assignment else None)
                print(f"[DEBUG handle_charge_success] Resolved provider_id for payout trigger: ({provider_id})")
                if provider_id:
                    print(f"[DEBUG handle_charge_success] Dispatching process_provider_payout for task {task.id}, provider {provider_id}")
                    await self.payment_service.process_provider_payout(
                        task_id=task.id,
                        provider_id=provider_id,
                    )
                    print(f"[DEBUG handle_charge_success] Finished process_provider_payout dispatch")
            else:
                print(f"[DEBUG handle_charge_success] Task payment_status is already {task.payment_status}, skipping provider payout trigger.")
                await self.system_logger.info(
                    f"Task {task.id} payment status is already {task.payment_status}, skipping provider payout trigger.",
                    source="payments.webhook",
                )

        # 6. Dispatch payment success push notification to user
        if user_id:
            print(f"[DEBUG handle_charge_success] Dispatching payment success notification to user: {user_id}")
            try:
                await self.notification_service.notify(
                    recepients=[user_id],
                    title="Payment Successful",
                    body=f"Your payment of {amount} has been received successfully.",
                    type=NotificationType.PAYMENT_RECEIVED,
                    data={"transaction_id": transaction.id, "reference": reference},
                )
                print(f"[DEBUG handle_charge_success] Notification dispatched successfully to user: {user_id}")
                await self.system_logger.info(
                    f"Dispatched payment success notification to user: {user_id}",
                    source="payments.webhook",
                )
            except Exception as e:
                print(f"[DEBUG handle_charge_success] Failed to dispatch notification: {str(e)}")
                await self.system_logger.error(
                    f"Failed to dispatch payment success notification to user {user_id}: {str(e)}",
                    source="payments.webhook",
                )

        print(f"[DEBUG handle_charge_success] END - Completed handle_charge_success for reference {reference}")

    async def handle_charge_failed(
        self,
        *,
        reference: str,
        amount: float,
        user_id: Optional[str] = None,
        task_id: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Handle failed payment charges."""
        if not reference or not user_id:
            await self.system_logger.error(
                "Missing required fields (reference or user_id) in charge.failed payload",
                source="payments.webhook",
            )
            return

        # Optimistic Concurrency Control: acquire lock on PayoutQueue if task_id present
        if task_id:
            payout_stmt = select(PayoutQueue).where(col(PayoutQueue.task_id) == task_id)
            payout_res = await self.payout_repo.execute(payout_stmt)
            payout = payout_res.first()
            if payout:
                prev_version = payout.lock_version
                lock_stmt = (
                    update(PayoutQueue)
                    .where(
                        col(PayoutQueue.id) == payout.id,
                        col(PayoutQueue.lock_version) == prev_version,
                    )
                    .values(
                        lock_version=prev_version + 1,
                        updated_at=lagos_now(),
                    )
                )
                lock_res = await self.payout_repo.execute(lock_stmt)
                if lock_res.rowcount == 0:
                    await self.system_logger.info(
                        f"Optimistic lock on PayoutQueue {payout.id} (version {prev_version}) could not be acquired. Another execution has the lock. Skipping.",
                        source="payments.webhook",
                    )
                    return
                payout.lock_version = prev_version + 1

        # Check idempotency for failed charge records
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

        # Create failed Transaction record
        transaction = Transaction(
            amount=amount,
            transaction_type=TransactionType.TASK_PAYMENT,
            status=TransactionStatus.FAILED,
            user_id=user_id,
            task_id=task_id,
            reference=reference,
            payment_mode="online",
            metadata_info=raw_data or {},
        )
        await self.transaction_repo.add(transaction)
        await self.system_logger.info(
            f"Created transaction for charge: {reference} with status: {TransactionStatus.FAILED}",
            source="payments.webhook",
        )

        # Dispatch payment failure notification to user
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


class TransferWebhookProcessor:
    """Processor responsible for handling outbound transfer/payout webhooks (transfer success/failed)."""

    def __init__(
        self,
        transaction_repo: Repository[Transaction],
        notification_service: NotificationService,
        system_logger: LoggerService,
        payout_repo: Repository[PayoutQueue],
        task_repo: Repository[Task],
        transfer_service: TransferService,
    ):
        self.transaction_repo = transaction_repo
        self.notification_service = notification_service
        self.system_logger = system_logger
        self.payout_repo = payout_repo
        self.task_repo = task_repo
        self.transfer_service = transfer_service

    async def process(
        self,
        event: str,
        *,
        reference: str,
        amount: float,
        user_id: str,
        task_id: Optional[str] = None,
        reason: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Route outgoing transfer event to the corresponding handler method."""
        print(
            f"[TransferWebhookProcessor] Processing event={event}, reference={reference}, amount={amount}, task_id={task_id}, user_id={user_id}"
        )
        try:
            timer = Timer()
            timer.start()

            # Delegate to specific transfer event handlers
            if event == "transfer.success":
                await self.handle_transfer_success(
                    reference=reference,
                    amount=amount,
                    user_id=user_id,
                    task_id=task_id,
                    raw_data=raw_data,
                )
            elif event == "transfer.failed":
                await self.handle_transfer_failed(
                    reference=reference,
                    amount=amount,
                    user_id=user_id,
                    task_id=task_id,
                    reason=reason,
                    raw_data=raw_data,
                )

            # Record system metric for processing execution time
            await self.system_logger.metric(
                f"Processed transfer webhook: {event}",
                timer.stop(),
                source="payments.webhook",
            )
            print(
                f"[TransferWebhookProcessor] Completed processing event={event} for reference={reference}"
            )
        except Exception as e:
            print(
                f"[TransferWebhookProcessor] ERROR processing event={event} for reference={reference}: {e}"
            )
            # Log error details and re-raise for upstream exception handling
            await self.system_logger.error(
                f"Error processing transfer webhook ({event}): {str(e)}",
                source="payments.webhook",
                metadata={"reference": reference, "event": event},
            )
            raise

    async def handle_transfer_success(
        self,
        *,
        reference: str,
        amount: float,
        user_id: str,
        task_id: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Handle successful outgoing provider payout transfers."""
        print(
            f"[TransferWebhookProcessor.handle_transfer_success] Starting for reference={reference}, task_id={task_id}, user_id={user_id}"
        )
        # Ensure mandatory reference and recipient user_id are present
        if not reference or not user_id:
            print(
                "[TransferWebhookProcessor.handle_transfer_success] Missing reference or user_id. Aborting."
            )
            await self.system_logger.error(
                "Missing required fields (reference or user_id) in transfer.success payload",
                source="payments.webhook",
            )
            return

        # Optimistic Concurrency Control: acquire lock on PayoutQueue
        payout_stmt = select(PayoutQueue).where(
            (col(PayoutQueue.reference) == reference)
            | (col(PayoutQueue.task_id) == task_id)
        )
        payout_res = await self.payout_repo.execute(payout_stmt)
        payout = payout_res.first()
        if payout:
            prev_version = payout.lock_version
            lock_stmt = (
                update(PayoutQueue)
                .where(
                    col(PayoutQueue.id) == payout.id,
                    col(PayoutQueue.lock_version) == prev_version,
                )
                .values(
                    lock_version=prev_version + 1,
                    updated_at=lagos_now(),
                )
            )
            lock_res = await self.payout_repo.execute(lock_stmt)
            if lock_res.rowcount == 0:
                print(
                    f"[TransferWebhookProcessor.handle_transfer_success] Could not acquire lock on PayoutQueue {payout.id}. Skipping."
                )
                await self.system_logger.info(
                    f"Optimistic lock on PayoutQueue {payout.id} (version {prev_version}) could not be acquired. Another execution has the lock. Skipping.",
                    source="payments.webhook",
                )
                return
            payout.lock_version = prev_version + 1
            print(
                f"[TransferWebhookProcessor.handle_transfer_success] Acquired lock on PayoutQueue {payout.id}"
            )

        # 1. Idempotency Check: skip processing if successful transfer transaction exists
        stmt = select(Transaction).where(
            col(Transaction.reference) == reference,
            col(Transaction.status) == TransactionStatus.SUCCESS,
        )
        existing_tx_res = await self.transaction_repo.execute(stmt)
        if existing_tx_res.first():
            print(
                f"[TransferWebhookProcessor.handle_transfer_success] Transaction for reference={reference} already exists. Skipping."
            )
            await self.system_logger.info(
                f"Transfer success for reference {reference} already processed. Skipping.",
                source="payments.webhook",
            )
            return

        print(
            f"[TransferWebhookProcessor.handle_transfer_success] Idempotency check passed for reference={reference}"
        )
        await self.system_logger.info(
            f"Processing successful transfer for reference: {reference}",
            source="payments.webhook",
        )

        # 2. Update Transfer record to COMPLETED via TransferService state machine
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
            print(
                f"[TransferWebhookProcessor.handle_transfer_success] Marking Transfer {transfer.id} as COMPLETED"
            )
            await self.transfer_service._mark_completed(
                transfer, provider_transfer_id=reference
            )
        else:
            print(
                f"[TransferWebhookProcessor.handle_transfer_success] No active Transfer record found. Using fallback raw update for PayoutQueue & Task."
            )
            # Fallback for PayoutQueue and Task records if Transfer record was not found
            where_clause = (
                (col(PayoutQueue.task_id) == task_id)
                if task_id
                else (col(PayoutQueue.reference) == reference)
            )
            res = await self.payout_repo.execute(
                update(PayoutQueue)
                .where(
                    where_clause,
                    col(PayoutQueue.status).in_(
                        [
                            PayoutStatus.PENDING,
                            PayoutStatus.CUSTOMER_PAID,
                            PayoutStatus.TRANSFER_INITIATED,
                        ]
                    ),
                )
                .values(status=PayoutStatus.COMPLETED)
            )
            print(
                f"[TransferWebhookProcessor.handle_transfer_success] Fallback raw update updated {res.rowcount} PayoutQueue row(s) to COMPLETED"
            )

            if task_id:
                task = await self.task_repo.get(task_id)
                if task and task.payment_status != PaymentStatus.PAID:
                    print(
                        f"[TransferWebhookProcessor.handle_transfer_success] Fallback: updating Task {task.id} payment_status to PAID"
                    )
                    task.payment_status = PaymentStatus.PAID
                    await self.task_repo.add(task)

        # 3. Create audit Transaction entry for provider payout
        transaction = Transaction(
            amount=-abs(amount),
            transaction_type=TransactionType.PROVIDER_PAYOUT,
            status=TransactionStatus.SUCCESS,
            user_id=user_id,
            task_id=task_id,
            reference=reference,
            metadata_info=raw_data or {},
        )
        await self.transaction_repo.add(transaction)
        print(
            f"[TransferWebhookProcessor.handle_transfer_success] Created Transaction {transaction.id} for reference={reference}"
        )
        await self.system_logger.info(
            f"Created transaction for transfer: {reference} with status: {TransactionStatus.SUCCESS}",
            source="payments.webhook",
        )

        # 4. Dispatch payout success push notification to user
        try:
            await self.notification_service.notify(
                recepients=[user_id],
                title="Payout Successful",
                body=f"Your payout of {amount} has been processed successfully.",
                type=NotificationType.PAYMENT_RECEIVED,
                data={"reference": reference},
            )
            print(
                f"[TransferWebhookProcessor.handle_transfer_success] Dispatched payout notification to user_id={user_id}"
            )
            await self.system_logger.info(
                f"Dispatched payout notification ({NotificationType.PAYMENT_RECEIVED}) to user: {user_id}",
                source="payments.webhook",
            )
        except Exception as e:
            print(
                f"[TransferWebhookProcessor.handle_transfer_success] Notification dispatch failed: {e}"
            )
            await self.system_logger.error(
                f"Failed to dispatch payout notification ({NotificationType.PAYMENT_RECEIVED}) to user {user_id}: {str(e)}",
                source="payments.webhook",
            )

    async def handle_transfer_failed(
        self,
        *,
        reference: str,
        amount: float,
        user_id: str,
        task_id: Optional[str] = None,
        reason: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Handle failed outgoing provider payout transfers."""
        # Ensure mandatory reference and recipient user_id are present
        if not reference or not user_id:
            await self.system_logger.error(
                "Missing required fields (reference or user_id) in transfer.failed payload",
                source="payments.webhook",
            )
            return

        # Optimistic Concurrency Control: acquire lock on PayoutQueue
        payout_stmt = select(PayoutQueue).where(
            (col(PayoutQueue.reference) == reference)
            | (col(PayoutQueue.task_id) == task_id)
        )
        payout_res = await self.payout_repo.execute(payout_stmt)
        payout = payout_res.first()
        if payout:
            prev_version = payout.lock_version
            lock_stmt = (
                update(PayoutQueue)
                .where(
                    col(PayoutQueue.id) == payout.id,
                    col(PayoutQueue.lock_version) == prev_version,
                )
                .values(
                    lock_version=prev_version + 1,
                    updated_at=lagos_now(),
                )
            )
            lock_res = await self.payout_repo.execute(lock_stmt)
            if lock_res.rowcount == 0:
                await self.system_logger.info(
                    f"Optimistic lock on PayoutQueue {payout.id} (version {prev_version}) could not be acquired. Another execution has the lock. Skipping.",
                    source="payments.webhook",
                )
                return
            payout.lock_version = prev_version + 1

        # Check idempotency for failed transfer records
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

        # Update Transfer record to FAILED via TransferService state machine
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
            fail_reason = reason or "Transfer failed via webhook notification"
            await self.transfer_service._mark_failed(
                transfer, code="WEBHOOK_FAILED", reason=fail_reason
            )
        else:
            # Fallback for PayoutQueue and Task records if Transfer record was not found
            where_clause = (
                (col(PayoutQueue.task_id) == task_id)
                if task_id
                else (col(PayoutQueue.reference) == reference)
            )
            res = await self.payout_repo.execute(
                update(PayoutQueue)
                .where(
                    where_clause,
                    col(PayoutQueue.status) != PayoutStatus.COMPLETED,
                )
                .values(status=PayoutStatus.CANCELLED)
            )
            print(
                f"[TransferWebhookProcessor.handle_transfer_failed] Fallback raw update updated {res.rowcount} PayoutQueue row(s) to CANCELLED"
            )

            if task_id:
                task = await self.task_repo.get(task_id)
                if task and task.payment_status != PaymentStatus.PAID:
                    task.payment_status = PaymentStatus.FAILED
                    await self.task_repo.add(task)

        # Create failed Transaction record for payout
        transaction = Transaction(
            amount=-abs(amount),
            transaction_type=TransactionType.PROVIDER_PAYOUT,
            status=TransactionStatus.FAILED,
            user_id=user_id,
            task_id=task_id,
            reference=reference,
            metadata_info=raw_data or {},
        )
        await self.transaction_repo.add(transaction)
        await self.system_logger.info(
            f"Created transaction for transfer: {reference} with status: {TransactionStatus.FAILED}",
            source="payments.webhook",
        )

        # Dispatch payout failure notification to user
        try:
            await self.notification_service.notify(
                recepients=[user_id],
                title="Payout Failed",
                body="There was an issue processing your payout. Please check your details.",
                type=NotificationType.PAYMENT_FAILED,
                data={"reference": reference},
            )
            await self.system_logger.info(
                f"Dispatched payout notification ({NotificationType.PAYMENT_FAILED}) to user: {user_id}",
                source="payments.webhook",
            )
        except Exception as e:
            await self.system_logger.error(
                f"Failed to dispatch payout notification ({NotificationType.PAYMENT_FAILED}) to user {user_id}: {str(e)}",
                source="payments.webhook",
            )


# ── Dependency Provider Functions ─────────────────────────────────────────────


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
    """FastAPI dependency provider for PaymentWebhookProcessor."""
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
    """FastAPI dependency provider for TransferWebhookProcessor."""
    return TransferWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
        system_logger=system_logger,
        payout_repo=payout_repo,
        task_repo=task_repo,
        transfer_service=transfer_service,
    )
