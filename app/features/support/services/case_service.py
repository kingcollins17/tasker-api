import random
from datetime import timedelta
from typing import Optional

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
    SupportCase,
)
from app.core.utils.datetime_helper import lagos_now
from app.features.support.schemas import (
    SupportCaseCreate,
    SupportCaseUpdate,
)


class SupportCaseService:
    """Service handling support case lifecycle and state transitions."""

    def __init__(self, session: AsyncSession):
        self.session = session

    def _generate_case_number(self) -> str:
        num = random.randint(100000, 999999)
        return f"SUP-{num}"

    async def create_case(
        self,
        user_id: str,
        is_customer: bool,
        schema: SupportCaseCreate,
    ) -> SupportCase:
        now = lagos_now()
        case_num = self._generate_case_number()
        reply_token = f"reply-{case_num}-{random.randint(1000, 9999)}"

        first_response_due = now + timedelta(hours=4)
        resolution_due = now + timedelta(hours=24)

        customer_id = user_id if is_customer else None
        provider_id = user_id if not is_customer else None

        case = SupportCase(
            case_number=case_num,
            type=schema.type,
            status=CaseStatus.OPEN,
            priority=schema.priority,
            customer_id=customer_id,
            provider_id=provider_id,
            task_id=schema.task_id,
            assignment_id=schema.assignment_id,
            payout_id=schema.payout_id,
            subject=schema.subject,
            description=schema.description,
            reply_token=reply_token,
            first_response_due_at=first_response_due,
            resolution_due_at=resolution_due,
            created_at=now,
            updated_at=now,
        )
        self.session.add(case)
        await self.session.flush()

        event = CaseEvent(
            case_id=case.id,
            event_type=CaseEventType.CASE_CREATED,
            actor_type="CUSTOMER" if is_customer else "PROVIDER",
            actor_id=user_id,
            event_metadata={
                "case_number": case.case_number,
                "type": case.type.value,
                "subject": case.subject,
            },
            created_at=now,
        )
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(case)
        return case

    async def update_case(
        self,
        case_id: str,
        schema: SupportCaseUpdate,
        actor_id: str,
        actor_type: str,
    ) -> Optional[SupportCase]:
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
        if schema.status and schema.status != case.status:
            old_status = case.status
            case.status = schema.status
            event = CaseEvent(
                case_id=case.id,
                event_type=CaseEventType.STATUS_CHANGED,
                actor_type=actor_type,
                actor_id=actor_id,
                event_metadata={"from_status": old_status.value, "to_status": schema.status.value},
                created_at=now,
            )
            self.session.add(event)

        if schema.priority and schema.priority != case.priority:
            old_priority = case.priority
            case.priority = schema.priority
            event = CaseEvent(
                case_id=case.id,
                event_type=CaseEventType.PRIORITY_CHANGED,
                actor_type=actor_type,
                actor_id=actor_id,
                event_metadata={"from_priority": old_priority.value, "to_priority": schema.priority.value},
                created_at=now,
            )
            self.session.add(event)

        if schema.subject:
            case.subject = schema.subject
        if schema.description:
            case.description = schema.description

        case.updated_at = now
        self.session.add(case)
        await self.session.commit()
        await self.session.refresh(case)
        return case

    async def close_case(self, case_id: str, actor_id: str, actor_type: str) -> Optional[SupportCase]:
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
        case.status = CaseStatus.CLOSED
        case.closed_at = now
        case.updated_at = now
        self.session.add(case)

        event = CaseEvent(
            case_id=case.id,
            event_type=CaseEventType.CASE_CLOSED,
            actor_type=actor_type,
            actor_id=actor_id,
            created_at=now,
        )
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(case)
        return case

    async def reopen_case(self, case_id: str, actor_id: str, actor_type: str) -> Optional[SupportCase]:
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
        case.status = CaseStatus.OPEN
        case.updated_at = now
        self.session.add(case)

        event = CaseEvent(
            case_id=case.id,
            event_type=CaseEventType.CASE_REOPENED,
            actor_type=actor_type,
            actor_id=actor_id,
            created_at=now,
        )
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(case)
        return case


def get_support_case_service(session: AsyncSession = Depends(get_session)) -> SupportCaseService:
    return SupportCaseService(session)
