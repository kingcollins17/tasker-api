from app.core.utils.timer import Timer
from app.core.services.logger_service import get_logger_service_manual

from celery import shared_task

from app.core.database import async_session_maker
from app.core.logging import logger
from app.core.utils.celery import run_async
from app.features.payments.services import get_payment_service_manual


@shared_task(name="payments.process_task_payment")
def process_task_payment(task_id: str, provider_id: str, payment_mode: str = "cash"):
    """Celery task to handle task payment processing (cash debt ledger entry vs online link generation)."""
    logger.info(
        f"process_task_payment: task_id={task_id}, provider_id={provider_id}, payment_mode={payment_mode}"
    )
    return run_async(_process_task_payment_async(task_id, provider_id, payment_mode))


@shared_task(name="payments.process_provider_payout")
def process_provider_payout(task_id: str, provider_id: str, payout_amount: float):
    """Celery task to handle transferring net payout to provider, offsetting any pending debt balance first."""
    logger.info(
        f"process_provider_payout: task_id={task_id}, provider_id={provider_id}, payout_amount={payout_amount}"
    )
    return run_async(
        _process_provider_payout_async(task_id, provider_id, payout_amount)
    )


@shared_task(name="payments.process_debt_settlement")
def process_debt_settlement(provider_id: str, amount_paid: float, reference: str):
    """Celery task to insert negative debt ledger entry for paid debt amount and log revenue Transaction."""
    logger.info(
        f"process_debt_settlement: provider_id={provider_id}, amount_paid={amount_paid}, ref={reference}"
    )
    return run_async(
        _process_debt_settlement_async(provider_id, amount_paid, reference)
    )


async def _process_task_payment_async(
    task_id: str, provider_id: str, payment_mode: str
) -> None:
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            service = get_payment_service_manual(session)
            await service.process_task_payment(task_id, provider_id, payment_mode)

            await system_logger.metric(
                "process_task_payment",
                timer.stop(),
                source="celery.process_task_payment",
            )
        except Exception as e:
            await system_logger.error(
                f"process_task_payment Failed: {str(e)}",
                source="celery.process_task_payment",
            )
            raise e


async def _process_provider_payout_async(
    task_id: str, provider_id: str, payout_amount: float
) -> None:
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            service = get_payment_service_manual(session)
            await service.process_provider_payout(task_id, provider_id, payout_amount)

            await system_logger.metric(
                "process_provider_payout",
                timer.stop(),
                source="celery.process_provider_payout",
            )
        except Exception as e:
            await system_logger.error(
                f"process_provider_payout Failed: {str(e)}",
                source="celery.process_provider_payout",
            )
            raise e


async def _process_debt_settlement_async(
    provider_id: str, amount_paid: float, reference: str
) -> None:
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            service = get_payment_service_manual(session)
            await service.process_debt_settlement(provider_id, amount_paid, reference)

            await system_logger.metric(
                "process_debt_settlement",
                timer.stop(),
                source="celery.process_debt_settlement",
            )
        except Exception as e:
            await system_logger.error(
                f"process_debt_settlement Failed: {str(e)}",
                source="celery.process_debt_settlement",
            )
            raise e
