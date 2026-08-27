from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core.models.payments import DebtReason, ProviderDebt, PayoutQueue, PayoutStatus
from app.core.models.tasks import PaymentMode, PaymentStatus, Task
from app.core.models.transactions import Transaction, TransactionStatus, TransactionType
from app.core.models.users import User
from app.core.repository import Repository
from app.core.models.transfers import Transfer, TransferStatus
from app.core.services.payment import (
    PaystackPaymentGateway,
    PermanentProviderError,
    TemporaryProviderError,
    TransferResult,
)
from app.features.payments.processors import PaymentWebhookProcessor, TransferWebhookProcessor
from app.features.payments.services import PaymentService
from app.features.payments.transfer_service import TransferService


@pytest.fixture
def mock_payment_deps():
    task_repo = MagicMock(spec=Repository)
    user_repo = MagicMock(spec=Repository)
    transaction_repo = MagicMock(spec=Repository)
    debt_repo = MagicMock(spec=Repository)
    payout_queue_repo = MagicMock(spec=Repository)
    notification_service = AsyncMock()
    payment_gateway = MagicMock()
    transfer_service = MagicMock()

    task_repo.get = AsyncMock()
    task_repo.add = AsyncMock()
    user_repo.get = AsyncMock()
    transaction_repo.add = AsyncMock()
    transaction_repo.execute = AsyncMock()
    debt_repo.add = AsyncMock()
    debt_repo.execute = AsyncMock()
    payout_queue_repo.add = AsyncMock()
    payout_queue_repo.get = AsyncMock()
    payout_queue_repo.update = AsyncMock()
    default_payout_res = MagicMock()
    default_payout_res.first.return_value = None
    default_payout_res.rowcount = 1
    payout_queue_repo.execute = AsyncMock(return_value=default_payout_res)
    payout_queue_repo.get_all = AsyncMock()

    return (
        task_repo,
        user_repo,
        transaction_repo,
        debt_repo,
        payout_queue_repo,
        notification_service,
        payment_gateway,
        transfer_service,
    )


@pytest.mark.asyncio
async def test_get_provider_debt_summary(mock_payment_deps):
    task_repo, user_repo, transaction_repo, debt_repo, payout_queue_repo, notification_service, payment_gateway, transfer_service = mock_payment_deps
    service = PaymentService(
        task_repo=task_repo,
        user_repo=user_repo,
        transaction_repo=transaction_repo,
        debt_repo=debt_repo,
        payout_queue_repo=payout_queue_repo,
        notification_service=notification_service,
        payment_gateway=payment_gateway,
        transfer_service=transfer_service,
    )

    mock_sum_res = MagicMock()
    mock_sum_res.one_or_none.return_value = 1300.0
    mock_count_res = MagicMock()
    mock_count_res.one_or_none.return_value = 2

    debt_repo.execute.side_effect = [mock_sum_res, mock_count_res]

    summary = await service.get_provider_debt_summary("p1")

    assert summary.total_debt_owed == 1300.0
    assert summary.pending_debts_count == 2


@pytest.mark.asyncio
async def test_initialize_debt_settlement_prevents_paying_more_than_owed(mock_payment_deps):
    task_repo, user_repo, transaction_repo, debt_repo, payout_queue_repo, notification_service, payment_gateway, transfer_service = mock_payment_deps
    service = PaymentService(
        task_repo=task_repo,
        user_repo=user_repo,
        transaction_repo=transaction_repo,
        debt_repo=debt_repo,
        payout_queue_repo=payout_queue_repo,
        notification_service=notification_service,
        payment_gateway=payment_gateway,
        transfer_service=transfer_service,
    )

    mock_sum_res = MagicMock()
    mock_sum_res.one_or_none.return_value = 1000.0
    mock_count_res = MagicMock()
    mock_count_res.one_or_none.return_value = 1
    debt_repo.execute.side_effect = [mock_sum_res, mock_count_res]

    # Requesting to pay 2000.0 when owing 1000.0 should raise HTTPException 400
    with pytest.raises(HTTPException) as exc_info:
        await service.initialize_debt_settlement("p1", request_amount=2000.0)

    assert exc_info.value.status_code == 400
    assert "cannot exceed total debt owed" in exc_info.value.detail


@pytest.mark.asyncio
async def test_initialize_debt_settlement_success(mock_payment_deps):
    task_repo, user_repo, transaction_repo, debt_repo, payout_queue_repo, notification_service, payment_gateway, transfer_service = mock_payment_deps
    service = PaymentService(
        task_repo=task_repo,
        user_repo=user_repo,
        transaction_repo=transaction_repo,
        debt_repo=debt_repo,
        payout_queue_repo=payout_queue_repo,
        notification_service=notification_service,
        payment_gateway=payment_gateway,
        transfer_service=transfer_service,
    )

    mock_sum_res = MagicMock()
    mock_sum_res.one_or_none.return_value = 1000.0
    mock_count_res = MagicMock()
    mock_count_res.one_or_none.return_value = 1
    debt_repo.execute.side_effect = [mock_sum_res, mock_count_res]

    provider = User(id="p1", email="provider@example.com", hashed_password="", type="provider")
    user_repo.get.return_value = provider

    mock_resp = MagicMock()
    mock_resp.checkout_url = "https://checkout.paystack.com/debt123"
    mock_resp.reference = "ref_debt_123"

    payment_gateway.receive_payment = AsyncMock(return_value=mock_resp)

    res = await service.initialize_debt_settlement("p1", request_amount=500.0)

    assert res.payment_url == "https://checkout.paystack.com/debt123"
    assert res.reference == "ref_debt_123"
    assert res.amount_to_pay == 500.0
    assert res.total_debt_owed == 1000.0


# ── Webhook Processors Tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_payment_processor_idempotency(mock_payment_deps):
    task_repo, user_repo, transaction_repo, debt_repo, payout_queue_repo, notification_service, payment_gateway, transfer_service = mock_payment_deps
    system_logger = AsyncMock()
    payment_service = MagicMock()

    processor = PaymentWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
        task_repo=task_repo,
        debt_repo=debt_repo,
        system_logger=system_logger,
        payout_repo=payout_queue_repo,
        transfer_service=transfer_service,
        payment_service=payment_service,
    )

    # Mock existing transaction found
    existing_res = MagicMock()
    existing_res.first.return_value = Transaction(id="tx1", reference="ref_existing", status=TransactionStatus.SUCCESS, amount=5000.0, transaction_type=TransactionType.TASK_PAYMENT)
    transaction_repo.execute.return_value = existing_res

    payload = {
        "reference": "ref_existing",
        "amount": 5000.0,
        "metadata": {"type": "task_payment", "task_id": "t1"},
    }

    await processor.process("charge.success", reference="ref_existing", amount=5000.0, task_id="t1", event_type="task_payment", raw_data=payload)

    # Should not create duplicate transaction
    transaction_repo.add.assert_not_called()
    system_logger.info.assert_called_with(
        "Charge success webhook for reference ref_existing already processed. Skipping.",
        source="payments.webhook",
    )


@pytest.mark.asyncio
async def test_payment_processor_exception_propagation(mock_payment_deps):
    task_repo, user_repo, transaction_repo, debt_repo, payout_queue_repo, notification_service, payment_gateway, transfer_service = mock_payment_deps
    system_logger = AsyncMock()
    payment_service = MagicMock()

    processor = PaymentWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
        task_repo=task_repo,
        debt_repo=debt_repo,
        system_logger=system_logger,
        payout_repo=payout_queue_repo,
        transfer_service=transfer_service,
        payment_service=payment_service,
    )

    # DB execution throws Exception
    transaction_repo.execute.side_effect = Exception("DB Connection Lost")

    payload = {
        "reference": "ref_fail",
        "amount": 5000.0,
    }

    with pytest.raises(Exception) as exc_info:
        await processor.process("charge.success", reference="ref_fail", amount=5000.0, raw_data=payload)

    assert "DB Connection Lost" in str(exc_info.value)
    system_logger.error.assert_called()


@pytest.mark.asyncio
async def test_payment_processor_state_machine_protection(mock_payment_deps):
    task_repo, user_repo, transaction_repo, debt_repo, payout_queue_repo, notification_service, payment_gateway, transfer_service = mock_payment_deps
    system_logger = AsyncMock()
    payment_service = MagicMock()

    processor = PaymentWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
        task_repo=task_repo,
        debt_repo=debt_repo,
        system_logger=system_logger,
        payout_repo=payout_queue_repo,
        transfer_service=transfer_service,
        payment_service=payment_service,
    )

    # Mock no existing transaction
    empty_res = MagicMock()
    empty_res.first.return_value = None
    transaction_repo.execute.return_value = empty_res

    # Task is ALREADY CUSTOMER_PAID
    task = Task(
        id="t1",
        title="Test Task",
        description="Test",
        payment_status=PaymentStatus.CUSTOMER_PAID,
        customer_total_price=5000.0,
        assigned_provider_id="p1",
    )
    task_repo.get.return_value = task

    payload = {
        "reference": "ref_duplicate_hook",
        "amount": 5000.0,
        "metadata": {"type": "task_payment", "task_id": "t1", "user_id": "u1"},
    }

    await processor.process("charge.success", reference="ref_duplicate_hook", amount=5000.0, user_id="u1", task_id="t1", event_type="task_payment", raw_data=payload)

    # Transaction is created
    transaction_repo.add.assert_called_once()
    # BUT payout is NOT re-triggered
    payment_service.process_provider_payout.assert_not_called()


@pytest.mark.asyncio
async def test_transfer_processor_idempotency(mock_payment_deps):
    task_repo, user_repo, transaction_repo, debt_repo, payout_queue_repo, notification_service, payment_gateway, transfer_service = mock_payment_deps
    system_logger = AsyncMock()

    processor = TransferWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
        system_logger=system_logger,
        payout_repo=payout_queue_repo,
        task_repo=task_repo,
        transfer_service=transfer_service,
    )

    existing_res = MagicMock()
    existing_res.first.return_value = Transaction(id="tx2", reference="ref_tr_123", status=TransactionStatus.SUCCESS, amount=-5000.0, transaction_type=TransactionType.PROVIDER_PAYOUT)
    transaction_repo.execute.return_value = existing_res

    payload = {
        "reference": "ref_tr_123",
        "amount": 5000.0,
        "metadata": {"user_id": "u1", "task_id": "t1"},
    }

    await processor.process("transfer.success", reference="ref_tr_123", amount=5000.0, user_id="u1", task_id="t1", raw_data=payload)

    transaction_repo.add.assert_not_called()


@pytest.mark.asyncio
async def test_transfer_processor_marks_transfer_completed(mock_payment_deps):
    task_repo, user_repo, transaction_repo, debt_repo, payout_queue_repo, notification_service, payment_gateway, transfer_service = mock_payment_deps
    system_logger = AsyncMock()

    transfer_repo = MagicMock()
    transfer_service.transfer_repo = transfer_repo
    transfer_service._mark_completed = AsyncMock()

    # No existing transaction
    empty_tx_res = MagicMock()
    empty_tx_res.first.return_value = None
    transaction_repo.execute.return_value = empty_tx_res

    # Found Transfer record
    transfer = Transfer(id="tr_10", task_id="t1", payment_id="p1", amount=5000.0, status=TransferStatus.PROCESSING, idempotency_key="transfer:tr_10")
    transfer_res = MagicMock()
    transfer_res.first.return_value = transfer
    transfer_repo.execute = AsyncMock(return_value=transfer_res)

    processor = TransferWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
        system_logger=system_logger,
        payout_repo=payout_queue_repo,
        task_repo=task_repo,
        transfer_service=transfer_service,
    )

    payload = {
        "reference": "TRF_999",
        "amount": 5000.0,
        "metadata": {"user_id": "u1", "task_id": "t1"},
    }

    await processor.process("transfer.success", reference="TRF_999", amount=5000.0, user_id="u1", task_id="t1", raw_data=payload)

    transfer_service._mark_completed.assert_called_once_with(transfer, provider_transfer_id="TRF_999")



@pytest.mark.asyncio
async def test_transfer_service_mark_completed_updates_db_records(mock_payment_deps):
    task_repo, user_repo, transaction_repo, debt_repo, payout_queue_repo, notification_service, payment_gateway, _ = mock_payment_deps
    transfer_repo = MagicMock(spec=Repository)
    attempt_repo = MagicMock(spec=Repository)
    payment_account_repo = MagicMock(spec=Repository)

    transfer_repo.commit = AsyncMock()

    payout = PayoutQueue(id="pay_1", payout_amount=5000.0, status=PayoutStatus.TRANSFER_INITIATED)
    payout_queue_repo.get.return_value = payout
    payout_queue_repo.add = AsyncMock()

    task = Task(id="task_1", title="Test", description="Test", payment_status=PaymentStatus.TRANSFER_INITIATED)
    task_repo.get.return_value = task
    task_repo.add = AsyncMock()

    service = TransferService(
        transfer_repo=transfer_repo,
        attempt_repo=attempt_repo,
        payout_queue_repo=payout_queue_repo,
        payment_account_repo=payment_account_repo,
        transfer_provider=payment_gateway,
        task_repo=task_repo,
    )

    transfer = Transfer(
        id="tr_100",
        task_id="task_1",
        payment_id="pay_1",
        provider_id="prov_1",
        amount=5000.0,
        status=TransferStatus.PROCESSING,
        idempotency_key="transfer:tr_100",
    )

    await service._mark_completed(transfer, provider_transfer_id="TRF_PAYSTACK_999")

    assert transfer.status == TransferStatus.COMPLETED
    assert transfer.provider_transfer_id == "TRF_PAYSTACK_999"
    assert payout.status == PayoutStatus.COMPLETED
    assert payout.reference == "TRF_PAYSTACK_999"
    assert task.payment_status == PaymentStatus.PAID
    payout_queue_repo.add.assert_called_with(payout)
    task_repo.add.assert_called_with(task)


@pytest.mark.asyncio
async def test_paystack_gateway_transfer_success():
    gateway = PaystackPaymentGateway(secret_key="sk_test_123")
    res = await gateway.transfer(
        amount=5000.0,
        currency="NGN",
        destination="RCP_123",
        idempotency_key="key_123",
        reference="ref_123",
    )
    assert isinstance(res, TransferResult)
    assert res.status == "success"
    assert res.provider_transfer_id == "ref_123"


@pytest.mark.asyncio
async def test_paystack_gateway_get_transfer_success():
    gateway = PaystackPaymentGateway(secret_key="sk_test_123")
    res = await gateway.get_transfer(provider_transfer_id="ref_123")
    assert isinstance(res, TransferResult)
    assert res.status == "success"
    assert res.provider_transfer_id == "ref_123"


@pytest.mark.asyncio
async def test_payment_processor_direct_keyword_params(mock_payment_deps):
    task_repo, user_repo, transaction_repo, debt_repo, payout_queue_repo, notification_service, payment_gateway, transfer_service = mock_payment_deps
    system_logger = AsyncMock()
    payment_service = MagicMock()

    task_repo.get = AsyncMock(return_value=Task(id="t1", title="Test", description="Test", customer_total_price=5000.0))

    processor = PaymentWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
        task_repo=task_repo,
        debt_repo=debt_repo,
        system_logger=system_logger,
        payout_repo=payout_queue_repo,
        transfer_service=transfer_service,
        payment_service=payment_service,
    )

    empty_res = MagicMock()
    empty_res.first.return_value = None
    transaction_repo.execute.return_value = empty_res

    await processor.handle_charge_success(
        reference="ref_direct_1",
        amount=5000.0,
        user_id="u1",
        task_id="t1",
        event_type="task_payment",
    )

    transaction_repo.add.assert_called_once()


@pytest.mark.asyncio
async def test_transfer_processor_direct_keyword_params(mock_payment_deps):
    task_repo, user_repo, transaction_repo, debt_repo, payout_queue_repo, notification_service, payment_gateway, transfer_service = mock_payment_deps
    system_logger = AsyncMock()

    transfer_repo = MagicMock()
    empty_tr_res = MagicMock()
    empty_tr_res.first.return_value = None
    transfer_repo.execute = AsyncMock(return_value=empty_tr_res)
    transfer_service.transfer_repo = transfer_repo

    processor = TransferWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
        system_logger=system_logger,
        payout_repo=payout_queue_repo,
        task_repo=task_repo,
        transfer_service=transfer_service,
    )

    empty_res = MagicMock()
    empty_res.first.return_value = None
    transaction_repo.execute.return_value = empty_res
    payout_queue_repo.get_all.return_value = []

    await processor.handle_transfer_success(
        reference="ref_tr_direct",
        amount=5000.0,
        user_id="u1",
        task_id="t1",
    )

    transaction_repo.add.assert_called_once()


@pytest.mark.asyncio
async def test_payout_queue_optimistic_locking_conflict(mock_payment_deps):
    task_repo, user_repo, transaction_repo, debt_repo, payout_queue_repo, notification_service, payment_gateway, transfer_service = mock_payment_deps
    system_logger = AsyncMock()
    payment_service = MagicMock()

    processor = PaymentWebhookProcessor(
        transaction_repo=transaction_repo,
        notification_service=notification_service,
        task_repo=task_repo,
        debt_repo=debt_repo,
        system_logger=system_logger,
        payout_repo=payout_queue_repo,
        transfer_service=transfer_service,
        payment_service=payment_service,
    )

    # Mock PayoutQueue object existing with lock_version=1
    payout = PayoutQueue(id="p1", task_id="t1", payout_amount=5000.0, lock_version=1)
    payout_res = MagicMock()
    payout_res.first.return_value = payout

    # Lock update returns rowcount 0 (conflict, lock not acquired)
    lock_res = MagicMock()
    lock_res.rowcount = 0

    payout_queue_repo.execute.side_effect = [payout_res, lock_res]

    await processor.handle_charge_success(
        reference="ref_conflict",
        amount=5000.0,
        user_id="u1",
        task_id="t1",
        event_type="task_payment",
    )

    # Should exit early due to lock conflict without adding transaction
    transaction_repo.add.assert_not_called()





