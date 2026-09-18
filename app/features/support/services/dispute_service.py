import random
from datetime import timedelta
from typing import Optional, Tuple

from fastapi import Depends
from sqlmodel import col, select, or_
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.models.support import (
    CaseEvent,
    CaseEventType,
    CasePriority,
    CaseStatus,
    CaseType,
    Dispute,
    SupportCase,
)
from app.core.utils.datetime_helper import lagos_now
from app.features.support.schemas import DisputeCreate


class DisputeService:
    """Service handling task dispute initiation, tracking, and escalation."""

    def __init__(self, session: AsyncSession):
        self.session = session

    def _generate_case_number(self) -> str:
        num = random.randint(100000, 999999)
        return f"DSP-{num}"

    async def open_dispute(
        self,
        user_id: str,
        is_customer: bool,
        schema: DisputeCreate,
    ) -> Tuple[SupportCase, Dispute]:
        now = lagos_now()
        case_num = self._generate_case_number()
        reply_token = f"reply-{case_num}-{random.randint(1000, 9999)}"

        customer_id = user_id if is_customer else None
        provider_id = user_id if not is_customer else None

        case = SupportCase(
            case_number=case_num,
            type=CaseType.DISPUTE,
            status=CaseStatus.OPEN,
            priority=CasePriority.HIGH,
            customer_id=customer_id,
            provider_id=provider_id,
            task_id=schema.task_id,
            assignment_id=schema.assignment_id,
            payout_id=schema.payout_id,
            subject=f"Dispute on Task #{schema.task_id[:8]}",
            description=schema.reason,
            reply_token=reply_token,
            first_response_due_at=now + timedelta(hours=2),
            resolution_due_at=now + timedelta(hours=24),
            created_at=now,
            updated_at=now,
        )
        self.session.add(case)
        await self.session.flush()

        dispute = Dispute(
            case_id=case.id,
            task_id=schema.task_id,
            assignment_id=schema.assignment_id,
            payout_id=schema.payout_id,
            initiated_by=user_id,
            reason=schema.reason,
            amount_disputed=schema.amount_disputed,
            currency="NGN",
            requested_resolution=schema.requested_resolution,
            created_at=now,
        )
        self.session.add(dispute)

        event = CaseEvent(
            case_id=case.id,
            event_type=CaseEventType.DISPUTE_OPENED,
            actor_type="CUSTOMER" if is_customer else "PROVIDER",
            actor_id=user_id,
            event_metadata={
                "dispute_id": dispute.id,
                "task_id": schema.task_id,
                "amount_disputed": schema.amount_disputed,
            },
            created_at=now,
        )
        self.session.add(event)

        await self.session.commit()
        await self.session.refresh(case)
        await self.session.refresh(dispute)
        return case, dispute

    async def escalate_dispute(self, case_id: str, agent_id: str) -> Optional[SupportCase]:
        stmt = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        res = await self.session.exec(stmt)
        case = res.first()
        if not case:
            return None

        now = lagos_now()
        case.priority = CasePriority.URGENT
        case.updated_at = now
        self.session.add(case)

        event = CaseEvent(
            case_id=case.id,
            event_type=CaseEventType.DISPUTE_ESCALATED,
            actor_type="AGENT",
            actor_id=agent_id,
            event_metadata={"priority": CasePriority.URGENT.value},
            created_at=now,
        )
        self.session.add(event)

        await self.session.commit()
        await self.session.refresh(case)
        return case


def get_dispute_service(session: AsyncSession = Depends(get_session)) -> DisputeService:
    return DisputeService(session)
