from app.core.utils.timer import Timer
from app.core.services.logger_service import get_logger_service_manual
from typing import Optional

from celery import shared_task
from sqlmodel import func, select

from app.core.database import async_session_maker
from app.core.logging import logger
from app.core.models.notifications import NotificationPriority, NotificationType
from app.core.models.payments import DebtReason, ProviderDebt
from app.core.models.tasks import PaymentMode, PaymentStatus, Task
from app.core.models.transactions import Transaction, TransactionStatus, TransactionType
from app.core.models.users import User
from app.core.repository import Repository
from app.core.services.payment import get_paystack_gateway
from app.core.utils.celery import run_async
from app.core.utils.datetime_helper import utc_now
from app.features.notifications.schemas import CreateNotification
from app.features.notifications.services import get_notification_service_manual


@shared_task(name="payments.process_task_payment")
def process_task_payment(task_id: str, provider_id: str, payment_mode: str = "cash"):
    """Celery task to handle task payment processing (cash debt ledger entry vs online link generation) independently."""
    logger.info(
        f"process_task_payment: task_id={task_id}, provider_id={provider_id}, payment_mode={payment_mode}"
    )
    return run_async(_process_task_payment_async(task_id, provider_id, payment_mode))


@shared_task(name="payments.process_provider_payout")
def process_provider_payout(task_id: str, provider_id: str, payout_amount: float):
    """Celery task to handle transferring net payout to provider, offsetting any pending debt balance first via ledger entry."""
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
            task_repo = Repository(Task, session)
            user_repo = Repository(User, session)
            debt_repo = Repository(ProviderDebt, session)
            notification_service = get_notification_service_manual(session)

            task = await task_repo.get(task_id)
            if not task:
                logger.error(f"_process_task_payment_async: task {task_id} not found")
                return

            mode = (
                PaymentMode(payment_mode)
                if payment_mode in ("cash", "online")
                else PaymentMode.CASH
            )
            task.payment_mode = mode

            if mode == PaymentMode.CASH:
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
                    await debt_repo.add(provider_debt)
                    logger.info(
                        f"Recorded cash debt entry (+₦{platform_fee:,.2f}) for provider {provider_id} on task {task.id}"
                    )

            elif mode == PaymentMode.ONLINE:
                customer = (
                    await user_repo.get(task.customer_id) if task.customer_id else None
                )
                customer_email = customer.email if customer else "customer@example.com"

                gateway = get_paystack_gateway()
                payment_resp = await gateway.receive_payment(
                    email=customer_email,
                    amount=task.customer_total_price or 0.0,
                    user_id=task.customer_id,
                    metadata={"task_id": task.id, "type": "task_payment"},
                )
                task.payment_url = payment_resp.checkout_url
                task.payment_status = PaymentStatus.PAYMENT_REQUESTED

                if task.customer_id:
                    purl = payment_resp.checkout_url or ""
                    amt_fmt = (
                        f"₦{task.customer_total_price:,.2f}"
                        if task.customer_total_price
                        else ""
                    )
                    await notification_service.create_notification(
                        CreateNotification(
                            type=NotificationType.TASK_ACCEPTED,
                            title="Payment Requested for Completed Task",
                            body=f"Your task '{task.title}' is completed. Tap to pay {amt_fmt} online.",
                            priority=NotificationPriority.HIGH,
                            recipient_ids=[task.customer_id],
                            channels=["in_app", "push", "email"],
                            data={
                                "task_id": task.id,
                                "payment_url": purl,
                                "amount": task.customer_total_price,
                                "type": "payment_request",
                            },
                        )
                    )
                logger.info(
                    f"Initialized online payment checkout link for task {task.id}: {task.payment_url}"
                )

            await task_repo.add(task)


            await system_logger.metric('process_task_payment', timer.stop(), source='celery.process_task_payment')
        except Exception as e:
            await system_logger.error(f'process_task_payment Failed: {str(e)}', source='celery.process_task_payment')
            raise e
async def _process_provider_payout_async(
    task_id: str, provider_id: str, payout_amount: float
) -> None:
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            debt_repo = Repository(ProviderDebt, session)
            tx_repo = Repository(Transaction, session)

            # 1. Calculate net debt balance using SUM(amount) from append-only ledger
            stmt = select(func.coalesce(func.sum(ProviderDebt.amount), 0.0)).where(
                ProviderDebt.provider_id == provider_id
            )
            total_debt = float((await debt_repo.execute(stmt)).scalar_one_or_none() or 0.0)

            remaining_payout = payout_amount
            debt_offset = 0.0

            if total_debt > 0.0:
                debt_offset = min(payout_amount, total_debt)
                remaining_payout = payout_amount - debt_offset

                # Append negative (-) debt ledger entry for payout offset
                offset_entry = ProviderDebt(
                    provider_id=provider_id,
                    task_id=task_id,
                    amount=-debt_offset,
                    reason=DebtReason.PAYOUT_OFFSET,
                    description=f"Automated debt offset from online task payout #{task_id}",
                )
                await debt_repo.add(offset_entry)

                # Log debt settlement transaction for revenue audit
                debt_settle_tx = Transaction(
                    amount=debt_offset,
                    transaction_type=TransactionType.DEBT_SETTLEMENT,
                    status=TransactionStatus.SUCCESS,
                    user_id=provider_id,
                    task_id=task_id,
                    metadata_info={"source": "payout_offset", "debt_offset": debt_offset},
                )
                await tx_repo.add(debt_settle_tx)

            # 2. Transfer net remaining payout via Paystack transfer API
            gateway = get_paystack_gateway()
            transfer_ref = f"payout_{task_id}_{int(utc_now().timestamp())}"

            if remaining_payout > 0:
                await gateway.send_payment(
                    amount=remaining_payout,
                    recipient_code=provider_id,
                    reference=transfer_ref,
                )

            logger.info(
                f"Processed payout for provider {provider_id} on task {task_id}: gross=₦{payout_amount:,.2f}, "
                f"debt_offset=₦{debt_offset:,.2f}, net_transferred=₦{remaining_payout:,.2f}"
            )


            await system_logger.metric('process_provider_payout', timer.stop(), source='celery.process_provider_payout')
        except Exception as e:
            await system_logger.error(f'process_provider_payout Failed: {str(e)}', source='celery.process_provider_payout')
            raise e
async def _process_debt_settlement_async(
    provider_id: str, amount_paid: float, reference: str
) -> None:
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            debt_repo = Repository(ProviderDebt, session)
            tx_repo = Repository(Transaction, session)

            # Append negative (-) debt ledger entry for debt payment
            payment_entry = ProviderDebt(
                provider_id=provider_id,
                amount=-amount_paid,
                reason=DebtReason.DEBT_PAYMENT,
                description=f"Online debt payment via reference {reference}",
            )
            await debt_repo.add(payment_entry)

            # Log Debt Settlement Revenue Transaction
            settlement_tx = Transaction(
                amount=amount_paid,
                transaction_type=TransactionType.DEBT_SETTLEMENT,
                status=TransactionStatus.SUCCESS,
                user_id=provider_id,
                reference=reference,
                payment_mode="online",
                metadata_info={"applied_amount": amount_paid},
            )
            await tx_repo.add(settlement_tx)

            logger.info(
                f"Processed debt settlement for provider {provider_id}: paid=₦{amount_paid:,.2f}, ref={reference}"
            )
            await system_logger.metric('process_debt_settlement', timer.stop(), source='celery.process_debt_settlement')
        except Exception as e:
            await system_logger.error(f'process_debt_settlement Failed: {str(e)}', source='celery.process_debt_settlement')
            raise e
