from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core.models.payments import DebtReason, ProviderDebt
from app.core.models.tasks import PaymentMode, PaymentStatus, Task
from app.core.models.transactions import Transaction, TransactionType
from app.core.models.users import User
from app.core.repository import Repository
from app.features.payments.services import PaymentService


@pytest.fixture
def mock_payment_deps():
    task_repo = MagicMock(spec=Repository)
    user_repo = MagicMock(spec=Repository)
    transaction_repo = MagicMock(spec=Repository)
    debt_repo = MagicMock(spec=Repository)
    payout_queue_repo = MagicMock(spec=Repository)
    notification_service = AsyncMock()

    task_repo.get = AsyncMock()
    task_repo.add = AsyncMock()
    user_repo.get = AsyncMock()
    transaction_repo.add = AsyncMock()
    debt_repo.add = AsyncMock()
    debt_repo.execute = AsyncMock()
    payout_queue_repo.add = AsyncMock()
    payout_queue_repo.get = AsyncMock()
    payout_queue_repo.update = AsyncMock()
    payout_queue_repo.execute = AsyncMock()
    payout_queue_repo.get_all = AsyncMock()

    return (
        task_repo,
        user_repo,
        transaction_repo,
        debt_repo,
        payout_queue_repo,
        notification_service,
    )


@pytest.mark.asyncio
async def test_get_provider_debt_summary(mock_payment_deps):
    task_repo, user_repo, transaction_repo, debt_repo, payout_queue_repo, notification_service = mock_payment_deps
    service = PaymentService(
        task_repo=task_repo,
        user_repo=user_repo,
        transaction_repo=transaction_repo,
        debt_repo=debt_repo,
        payout_queue_repo=payout_queue_repo,
        notification_service=notification_service,
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
    task_repo, user_repo, transaction_repo, debt_repo, payout_queue_repo, notification_service = mock_payment_deps
    service = PaymentService(
        task_repo=task_repo,
        user_repo=user_repo,
        transaction_repo=transaction_repo,
        debt_repo=debt_repo,
        payout_queue_repo=payout_queue_repo,
        notification_service=notification_service,
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
    task_repo, user_repo, transaction_repo, debt_repo, payout_queue_repo, notification_service = mock_payment_deps
    service = PaymentService(
        task_repo=task_repo,
        user_repo=user_repo,
        transaction_repo=transaction_repo,
        debt_repo=debt_repo,
        payout_queue_repo=payout_queue_repo,
        notification_service=notification_service,
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

    mock_gateway = MagicMock()
    mock_gateway.receive_payment = AsyncMock(return_value=mock_resp)

    with patch("app.features.payments.services.get_paystack_gateway", return_value=mock_gateway):
        res = await service.initialize_debt_settlement("p1", request_amount=500.0)

        assert res.payment_url == "https://checkout.paystack.com/debt123"
        assert res.reference == "ref_debt_123"
        assert res.amount_to_pay == 500.0
        assert res.total_debt_owed == 1000.0
