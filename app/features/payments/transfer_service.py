import random
from datetime import timedelta
from typing import Optional

from fastapi import Depends
from sqlmodel import select

from app.core.logging import logger
from app.core.models.payments import PayoutQueue, PayoutStatus
from app.core.models.tasks import PaymentStatus, Task
from app.core.models.transfers import (
    VALID_TRANSFER_TRANSITIONS,
    Transfer,
    TransferAttempt,
    TransferStatus,
)
from app.core.models.users import PaymentAccount
from app.core.repository import GetRepository, Repository
from app.core.services.transfer_provider import (
    PaystackTransferProvider,
    PermanentProviderError,
    TemporaryProviderError,
    get_transfer_provider,
)
from app.core.utils.datetime_helper import lagos_now

# ── Retry constants ───────────────────────────────────────────────────────────
BASE_DELAY_SECONDS = 30
MAX_DELAY_SECONDS = 7200  # 2 hours
JITTER_FACTOR = 0.2


class TransferService:
    """Durable state machine for provider money transfers.

    Guarantees that an intended money movement eventually reaches a known
    outcome (COMPLETED or FAILED) exactly once from the system's perspective.

    Key design principles:
    - Database is the source of truth, not the payment provider.
    - Performs ONE transfer attempt per call — does NOT own the retry loop.
    - Celery owns scheduling/retries; Postgres owns state.
    - Provider is treated as an unreliable external system.
    """

    def __init__(
        self,
        transfer_repo: Repository[Transfer],
        attempt_repo: Repository[TransferAttempt],
        payout_queue_repo: Repository[PayoutQueue],
        payment_account_repo: Repository[PaymentAccount],
        transfer_provider: PaystackTransferProvider,
        task_repo: Optional[Repository[Task]] = None,
    ):
        self.transfer_repo = transfer_repo
        self.attempt_repo = attempt_repo
        self.payout_queue_repo = payout_queue_repo
        self.payment_account_repo = payment_account_repo
        self.provider = transfer_provider
        self.task_repo = task_repo

    # ── Public API ────────────────────────────────────────────────────────

    async def create_transfer(
        self,
        *,
        payment_id: str,
        task_id: str,
        provider_id: str,
        amount: float,
        currency: str = "NGN",
    ) -> Transfer:
        """Create a new Transfer record in PENDING status.

        Idempotent — returns the existing Transfer if one already exists
        for the given payment_id (unique constraint on payment_id).
        """
        # Check for existing transfer (idempotency via unique payment_id)
        existing_stmt = select(Transfer).where(Transfer.payment_id == payment_id)
        existing = (await self.transfer_repo.execute(existing_stmt)).first()
        if existing:
            logger.info(
                f"Transfer already exists for payment_id={payment_id}: transfer_id={existing.id}"
            )
            return existing

        transfer = Transfer(
            task_id=task_id,
            payment_id=payment_id,
            provider_id=provider_id,
            amount=amount,
            currency=currency,
            status=TransferStatus.PENDING,
            idempotency_key="",  # Will be set to transfer:{id} after creation
        )
        # Set idempotency_key using the generated ID
        transfer.idempotency_key = f"transfer:{transfer.id}"

        transfer = await self.transfer_repo.add(transfer)
        logger.info(
            f"Created transfer {transfer.id} for payment_id={payment_id}, "
            f"provider={provider_id}, amount={amount} {currency}"
        )
        return transfer

    async def process_transfer(self, transfer_id: str) -> Transfer:
        """Execute ONE transfer attempt against the payment provider.

        This is the core operation of the state machine:
        1. Fetch & lock the transfer row (SELECT FOR UPDATE).
        2. Guard: skip if already COMPLETED or not in a processable state.
        3. Set PROCESSING, increment attempt_count, commit (release DB lock).
        4. Call the payment provider OUTSIDE the transaction.
        5. Mark COMPLETED, RETRYING, or FAILED based on the result.
        6. Record a TransferAttempt for audit.

        Returns the updated Transfer.
        """
        # ── 1. Acquire row lock ──────────────────────────────────────────
        lock_stmt = (
            select(Transfer)
            .where(Transfer.id == transfer_id)
            .with_for_update()
        )
        transfer = (await self.transfer_repo.execute(lock_stmt)).first()

        if not transfer:
            logger.error(f"Transfer {transfer_id} not found")
            raise ValueError(f"Transfer {transfer_id} not found")

        # ── 2. Guard against invalid states ──────────────────────────────
        if transfer.status == TransferStatus.COMPLETED:
            logger.info(f"Transfer {transfer_id} already completed, skipping")
            return transfer

        if transfer.status not in (
            TransferStatus.PENDING,
            TransferStatus.RETRYING,
        ):
            logger.warning(
                f"Transfer {transfer_id} in non-processable state: {transfer.status}"
            )
            return transfer

        # ── 3. Transition to PROCESSING ──────────────────────────────────
        self._transition(transfer, TransferStatus.PROCESSING)
        transfer.attempt_count += 1
        transfer.last_attempt_at = lagos_now()
        await self.transfer_repo.commit()

        # ── 4. Resolve provider recipient code ───────────────────────────
        destination = await self._resolve_destination(transfer.provider_id)
        if not destination:
            await self._mark_failed(
                transfer,
                code="NO_PAYMENT_ACCOUNT",
                reason=f"No active payment account found for provider {transfer.provider_id}",
            )
            await self._record_attempt(
                transfer,
                status="error",
                error_code="NO_PAYMENT_ACCOUNT",
                error_message="Provider has no active payment account",
            )
            return transfer

        # ── 5. Call payment provider (OUTSIDE transaction) ───────────────
        try:
            result = await self.provider.transfer(
                amount=transfer.amount,
                currency=transfer.currency,
                destination=destination,
                idempotency_key=transfer.idempotency_key,
                reference=f"payout_{transfer.task_id}_{transfer.id[:8]}",
            )

            # ── Success ──────────────────────────────────────────────
            await self._mark_completed(
                transfer,
                provider_transfer_id=result.provider_transfer_id,
            )
            await self._record_attempt(
                transfer,
                status="success",
                provider_transfer_id=result.provider_transfer_id,
            )
            logger.info(
                f"Transfer {transfer.id} completed: provider_ref={result.provider_transfer_id}"
            )

        except TemporaryProviderError as exc:
            # ── Retryable failure ────────────────────────────────────
            await self._mark_retry(transfer, code=exc.code, reason=str(exc))
            await self._record_attempt(
                transfer,
                status="temporary_error",
                error_code=exc.code,
                error_message=str(exc),
            )
            logger.warning(
                f"Transfer {transfer.id} temporary failure (attempt {transfer.attempt_count}): {exc}"
            )

        except PermanentProviderError as exc:
            # ── Non-retryable failure ────────────────────────────────
            await self._mark_failed(transfer, code=exc.code, reason=str(exc))
            await self._record_attempt(
                transfer,
                status="permanent_error",
                error_code=exc.code,
                error_message=str(exc),
            )
            logger.error(
                f"Transfer {transfer.id} permanent failure: {exc}"
            )

        return transfer

    async def reconcile_transfer(self, transfer_id: str) -> Transfer:
        """Query the provider for a stuck PROCESSING transfer and resolve its status.

        Used by the reconciliation worker for transfers that have been
        PROCESSING longer than expected (provider timeout / crash recovery).
        """
        transfer = await self.transfer_repo.get(transfer_id)
        if not transfer:
            raise ValueError(f"Transfer {transfer_id} not found")

        if transfer.status != TransferStatus.PROCESSING:
            return transfer

        try:
            # Try looking up by provider_transfer_id first, then idempotency_key
            lookup_key = transfer.provider_transfer_id or transfer.idempotency_key
            result = await self.provider.get_transfer(lookup_key)

            if result.status == "success":
                await self._mark_completed(
                    transfer,
                    provider_transfer_id=result.provider_transfer_id,
                )
                logger.info(f"Reconciled transfer {transfer.id} → COMPLETED")
            elif result.status == "failed":
                await self._mark_failed(
                    transfer,
                    code="RECONCILED_FAILED",
                    reason="Provider confirmed transfer failed during reconciliation",
                )
                logger.info(f"Reconciled transfer {transfer.id} → FAILED")
            else:
                # Provider doesn't know about it — retry
                await self._mark_retry(
                    transfer,
                    code="RECONCILED_UNKNOWN",
                    reason="Provider returned unknown status during reconciliation",
                )
                logger.info(f"Reconciled transfer {transfer.id} → RETRYING (unknown)")

        except TemporaryProviderError:
            # Can't reach provider — will try again on next reconciliation pass
            logger.warning(
                f"Cannot reconcile transfer {transfer.id}: provider unreachable"
            )

        return transfer

    # ── State transitions ─────────────────────────────────────────────────

    def _transition(self, transfer: Transfer, new_status: TransferStatus) -> None:
        """Enforce valid state machine transitions."""
        allowed = VALID_TRANSFER_TRANSITIONS.get(transfer.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Invalid transfer transition: {transfer.status} → {new_status}"
            )
        transfer.status = new_status
        transfer.version += 1
        transfer.updated_at = lagos_now()

    async def _mark_completed(
        self,
        transfer: Transfer,
        provider_transfer_id: Optional[str] = None,
    ) -> None:
        """Transition a transfer to COMPLETED and update associated DB records."""
        self._transition(transfer, TransferStatus.COMPLETED)
        transfer.provider_transfer_id = provider_transfer_id
        transfer.completed_at = lagos_now()
        transfer.next_retry_at = None
        await self.transfer_repo.commit()

        # Update associated PayoutQueue entry to COMPLETED
        if transfer.payment_id and self.payout_queue_repo:
            payout = await self.payout_queue_repo.get(transfer.payment_id)
            if payout:
                payout.status = PayoutStatus.COMPLETED
                if provider_transfer_id:
                    payout.reference = provider_transfer_id
                await self.payout_queue_repo.add(payout)

        # Update associated Task payment_status to PAID
        if transfer.task_id and self.task_repo:
            task = await self.task_repo.get(transfer.task_id)
            if task and task.payment_status != PaymentStatus.PAID:
                task.payment_status = PaymentStatus.PAID
                await self.task_repo.add(task)

    async def _mark_retry(
        self,
        transfer: Transfer,
        code: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Transition a transfer to RETRYING, or FAILED if max attempts exceeded."""
        if transfer.attempt_count >= transfer.max_attempts:
            await self._mark_failed(
                transfer,
                code=code or "MAX_ATTEMPTS_EXCEEDED",
                reason=reason or f"Exceeded maximum attempts ({transfer.max_attempts})",
            )
            return

        self._transition(transfer, TransferStatus.RETRYING)
        delay = self._calculate_retry_delay(transfer.attempt_count)
        transfer.next_retry_at = lagos_now() + timedelta(seconds=delay)
        transfer.failure_code = code
        transfer.failure_reason = reason
        await self.transfer_repo.commit()

    async def _mark_failed(
        self,
        transfer: Transfer,
        code: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Transition a transfer to FAILED (terminal state) and update associated DB records."""
        self._transition(transfer, TransferStatus.FAILED)
        transfer.failed_at = lagos_now()
        transfer.failure_code = code
        transfer.failure_reason = reason
        transfer.next_retry_at = None
        await self.transfer_repo.commit()

        # Update associated PayoutQueue entry to CANCELLED if not already COMPLETED
        if transfer.payment_id and self.payout_queue_repo:
            payout = await self.payout_queue_repo.get(transfer.payment_id)
            if payout and payout.status != PayoutStatus.COMPLETED:
                payout.status = PayoutStatus.CANCELLED
                await self.payout_queue_repo.add(payout)

        # Update associated Task payment_status to FAILED if not already PAID
        if transfer.task_id and self.task_repo:
            task = await self.task_repo.get(transfer.task_id)
            if task and task.payment_status != PaymentStatus.PAID:
                task.payment_status = PaymentStatus.FAILED
                await self.task_repo.add(task)

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _resolve_destination(self, provider_id: Optional[str]) -> Optional[str]:
        """Look up the provider's Paystack recipient code from PaymentAccount."""
        if not provider_id:
            return None
        stmt = select(PaymentAccount).where(
            PaymentAccount.user_id == provider_id,
            PaymentAccount.is_active == True,
        )
        account = (await self.payment_account_repo.execute(stmt)).first()
        if account and account.external_account_id:
            return account.external_account_id
        return None

    async def _record_attempt(
        self,
        transfer: Transfer,
        *,
        status: str,
        provider_transfer_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> TransferAttempt:
        """Create an immutable TransferAttempt audit record."""
        attempt = TransferAttempt(
            transfer_id=transfer.id,
            attempt_number=transfer.attempt_count,
            status=status,
            provider_transfer_id=provider_transfer_id,
            error_code=error_code,
            error_message=error_message,
            started_at=transfer.last_attempt_at or lagos_now(),
            completed_at=lagos_now(),
        )
        return await self.attempt_repo.add(attempt)

    @staticmethod
    def _calculate_retry_delay(attempt_count: int) -> float:
        """Exponential backoff with jitter.

        attempt 1 → ~30s
        attempt 2 → ~60s
        attempt 3 → ~120s
        ...
        capped at 7200s (2 hours)
        """
        delay = min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * (2 ** (attempt_count - 1)))
        jitter = random.uniform(0, delay * JITTER_FACTOR)
        return delay + jitter


# ── FastAPI Dependency Injection ──────────────────────────────────────────────


def get_transfer_service(
    transfer_repo: Repository[Transfer] = Depends(GetRepository(Transfer)),
    attempt_repo: Repository[TransferAttempt] = Depends(GetRepository(TransferAttempt)),
    payout_queue_repo: Repository[PayoutQueue] = Depends(GetRepository(PayoutQueue)),
    payment_account_repo: Repository[PaymentAccount] = Depends(GetRepository(PaymentAccount)),
    transfer_provider: PaystackTransferProvider = Depends(get_transfer_provider),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
) -> "TransferService":
    return TransferService(
        transfer_repo=transfer_repo,
        attempt_repo=attempt_repo,
        payout_queue_repo=payout_queue_repo,
        payment_account_repo=payment_account_repo,
        transfer_provider=transfer_provider,
        task_repo=task_repo,
    )


def get_transfer_service_manual(session) -> "TransferService":
    """Factory for constructing TransferService outside of FastAPI DI (e.g. Celery tasks)."""
    return TransferService(
        transfer_repo=Repository(Transfer, session),
        attempt_repo=Repository(TransferAttempt, session),
        payout_queue_repo=Repository(PayoutQueue, session),
        payment_account_repo=Repository(PaymentAccount, session),
        transfer_provider=get_transfer_provider(),
        task_repo=Repository(Task, session),
    )
