from celery import shared_task
from sqlmodel import select, col

from app.core.celery_database import celery_session_factory
from app.core.logging import logger
from app.core.models.transfers import Transfer, TransferStatus
from app.core.services.logger_service import get_logger_service_manual
from app.core.utils.celery import run_async
from app.core.utils.datetime_helper import lagos_now
from app.core.utils.timer import Timer
from app.features.payments.transfer_service import get_transfer_service_manual


# ── Process a single transfer attempt ─────────────────────────────────────────


@shared_task(name="transfers.process_transfer")
def process_transfer_task(transfer_id: str):
    """Celery task: execute ONE transfer attempt via TransferService.

    If the transfer ends up in RETRYING state, schedules itself again
    with the calculated retry delay (Celery countdown).
    """
    logger.info(f"process_transfer_task: transfer_id={transfer_id}")
    return run_async(_process_transfer_async(transfer_id))


async def _process_transfer_async(transfer_id: str) -> None:
    async with celery_session_factory() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            service = get_transfer_service_manual(session)
            transfer = await service.process_transfer(transfer_id)

            await system_logger.metric(
                f"process_transfer (status={transfer.status})",
                timer.stop(),
                source="celery.transfers.process_transfer",
            )

            # If the transfer needs retry, schedule the next attempt
            if transfer.status == TransferStatus.RETRYING and transfer.next_retry_at:
                delay_seconds = max(
                    0,
                    (transfer.next_retry_at - lagos_now()).total_seconds(),
                )
                logger.info(
                    f"Transfer {transfer_id} retrying in {delay_seconds:.0f}s "
                    f"(attempt {transfer.attempt_count}/{transfer.max_attempts})"
                )
                # pyrefly: ignore [not-callable]
                process_transfer_task.apply_async(
                    args=[transfer_id],
                    countdown=int(delay_seconds),
                )

        except Exception as e:
            await system_logger.error(
                f"process_transfer_task failed: {str(e)}",
                source="celery.transfers.process_transfer",
                metadata={"transfer_id": transfer_id},
            )
            raise e


# ── Recovery worker: find stuck/missed transfers ──────────────────────────────


async def _recover_stuck_transfers_async() -> None:
    async with celery_session_factory() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            service = get_transfer_service_manual(session)
            now = lagos_now()

            stmt = (
                select(Transfer)
                .where(
                    Transfer.status.in_([  # type: ignore
                        TransferStatus.PENDING,
                        TransferStatus.RETRYING,
                    ]),
                )
                .where(
                    # PENDING transfers with no next_retry_at are new and should be processed
                    # RETRYING transfers should only be processed when next_retry_at <= now
                    (col(Transfer.next_retry_at) == None) | (col(Transfer.next_retry_at) <= now)  # noqa: E711
                )
                .limit(100)
            )
            transfers = (await service.transfer_repo.execute(stmt)).all()

            enqueued = 0
            for transfer in transfers:
                # pyrefly: ignore [not-callable]
                process_transfer_task.delay(transfer.id)
                enqueued += 1

            if enqueued > 0:
                logger.info(f"Recovery worker enqueued {enqueued} stuck transfers")

            await system_logger.metric(
                f"recover_stuck_transfers (enqueued={enqueued})",
                timer.stop(),
                source="celery.transfers.recover_stuck_transfers",
            )

        except Exception as e:
            await system_logger.error(
                f"recover_stuck_transfers_task failed: {str(e)}",
                source="celery.transfers.recover_stuck_transfers",
            )
            raise e


@shared_task(name="transfers.recover_stuck_transfers_task")
def recover_stuck_transfers_task():
    """Celery worker task: find PENDING/RETRYING transfers whose retry time has passed."""
    logger.info("recover_stuck_transfers_task: scanning for eligible transfers")
    return run_async(_recover_stuck_transfers_async())


# ── Reconciliation worker: resolve ambiguous PROCESSING transfers ─────────────


async def _reconcile_processing_transfers_async() -> None:
    from datetime import timedelta

    async with celery_session_factory() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            service = get_transfer_service_manual(session)
            stale_cutoff = lagos_now() - timedelta(minutes=10)

            stmt = (
                select(Transfer)
                .where(
                    Transfer.status == TransferStatus.PROCESSING,
                    Transfer.updated_at < stale_cutoff,
                )
                .limit(50)
            )
            transfers = (await service.transfer_repo.execute(stmt)).all()

            reconciled = 0
            for transfer in transfers:
                await service.reconcile_transfer(transfer.id)
                reconciled += 1

            if reconciled > 0:
                logger.info(f"Reconciliation worker processed {reconciled} stale transfers")

            await system_logger.metric(
                f"reconcile_processing_transfers (reconciled={reconciled})",
                timer.stop(),
                source="celery.transfers.reconcile_processing_transfers",
            )

        except Exception as e:
            await system_logger.error(
                f"reconcile_processing_transfers_task failed: {str(e)}",
                source="celery.transfers.reconcile_processing_transfers",
            )
            raise e


@shared_task(name="transfers.reconcile_processing_transfers_task")
def reconcile_processing_transfers_task():
    """Celery worker task: resolve transfers stuck in PROCESSING for >10 minutes."""
    logger.info("reconcile_processing_transfers_task: scanning for stale transfers")
    return run_async(_reconcile_processing_transfers_async())

