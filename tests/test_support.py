import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.models.support import (
    CaseEventType,
    CaseMessage,
    CasePriority,
    CaseStatus,
    CaseType,
    Dispute,
    MessageChannel,
    MessageSenderType,
    MessageVisibility,
    SupportCase,
)
from app.features.support.schemas import (
    CaseAssignmentCreate,
    CaseMessageCreate,
    CaseResolutionCreate,
    DisputeCreate,
    SupportCaseCreate,
    SupportCaseUpdate,
)
from app.features.support.services.assignment_service import CaseAssignmentService
from app.features.support.services.case_service import SupportCaseService
from app.features.support.services.dispute_service import DisputeService
from app.features.support.services.message_service import CaseMessageService
from app.features.support.services.resolution_service import CaseResolutionService


@pytest.fixture
def mock_session():
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.exec = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_create_support_case(mock_session):
    service = SupportCaseService(mock_session)
    schema = SupportCaseCreate(
        subject="Payment Failed",
        description="My card was charged twice for the task",
        type=CaseType.PAYMENT,
        priority=CasePriority.HIGH,
        task_id="task-100",
    )

    case = await service.create_case(
        user_id="user-123",
        is_customer=True,
        schema=schema,
    )

    assert case is not None
    assert case.customer_id == "user-123"
    assert case.type == CaseType.PAYMENT
    assert case.priority == CasePriority.HIGH
    assert case.status == CaseStatus.OPEN
    assert case.case_number.startswith("SUP-")
    assert mock_session.add.call_count >= 2  # Case and Event added


@pytest.mark.asyncio
async def test_open_dispute(mock_session):
    service = DisputeService(mock_session)
    schema = DisputeCreate(
        task_id="task-200",
        reason="Provider did not complete the agreed work",
        amount_disputed=15000.0,
        requested_resolution="Full refund",
    )

    case, dispute = await service.open_dispute(
        user_id="cust-1",
        is_customer=True,
        schema=schema,
    )

    assert case.type == CaseType.DISPUTE
    assert case.priority == CasePriority.HIGH
    assert dispute.task_id == "task-200"
    assert dispute.amount_disputed == 15000.0
    assert dispute.initiated_by == "cust-1"
    assert mock_session.add.call_count >= 3  # Case, Dispute, and Event added


@pytest.mark.asyncio
async def test_send_case_message_and_idempotency(mock_session):
    mock_case = SupportCase(
        id="case-1",
        case_number="SUP-100001",
        subject="Test Case",
        description="Desc",
        status=CaseStatus.WAITING_FOR_USER,
    )

    mock_exec_res = MagicMock()
    mock_exec_res.first.side_effect = [None, mock_case]  # 1st query: no existing email msg ID; 2nd query: returns mock_case
    mock_session.exec.return_value = mock_exec_res

    service = CaseMessageService(mock_session)
    msg, updated_case = await service.send_message(
        case_id="case-1",
        sender_id="cust-1",
        sender_type=MessageSenderType.CUSTOMER,
        body="Here is the information requested",
        email_message_id="msg-id-12345",
    )

    assert msg is not None
    assert msg.body == "Here is the information requested"
    assert updated_case.status == CaseStatus.IN_PROGRESS  # Transitioned from WAITING_FOR_USER to IN_PROGRESS


@pytest.mark.asyncio
async def test_assign_case(mock_session):
    mock_case = SupportCase(
        id="case-10",
        case_number="SUP-100010",
        subject="Assignment Test",
        description="Desc",
        assigned_agent_id=None,
    )

    mock_exec_res = MagicMock()
    mock_exec_res.first.return_value = mock_case
    mock_exec_res.all.return_value = []
    mock_session.exec.return_value = mock_exec_res

    service = CaseAssignmentService(mock_session)
    case, assignment = await service.assign_case(
        case_id="case-10",
        agent_id="agent-77",
        assigned_by="admin-1",
        reason="Initial assignment to tier-1 support",
    )

    assert case.assigned_agent_id == "agent-77"
    assert assignment.agent_id == "agent-77"
    assert assignment.assigned_by == "admin-1"


@pytest.mark.asyncio
async def test_resolve_case(mock_session):
    mock_case = SupportCase(
        id="case-20",
        case_number="SUP-100020",
        subject="Resolution Test",
        description="Desc",
        status=CaseStatus.IN_PROGRESS,
    )

    mock_exec_res = MagicMock()
    mock_exec_res.first.return_value = mock_case
    mock_session.exec.return_value = mock_exec_res

    service = CaseResolutionService(mock_session)
    schema = CaseResolutionCreate(
        decision="Refund issued",
        reason="Customer was charged incorrectly",
    )

    case, resolution = await service.resolve_case(
        case_id="case-20",
        agent_id="agent-99",
        schema=schema,
    )

    assert case.status == CaseStatus.RESOLVED
    assert case.resolved_at is not None
    assert resolution.decision == "Refund issued"
    assert resolution.resolved_by == "agent-99"
