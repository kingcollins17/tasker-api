import random
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import col, select, func, or_, and_
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.models.support import (
    CaseEvent,
    CaseEventType,
    CaseMessage,
    CasePriority,
    CaseStatus,
    CaseType,
    MessageVisibility,
    SupportCase,
)
from app.core.utils.datetime_helper import lagos_now
from app.features.support.schemas import (
    SupportCaseCreate,
    SupportCaseUpdate,
    TimelineItemResponse,
)


class SupportCaseService:
    """Service handling support case lifecycle, querying, state transitions, and timeline compilation."""

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
        case_num = self. _generate_case_number()
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
            booking_id=schema.booking_id,
            payment_id=schema.payment_id,
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

    async def get_case(self, case_id: str) -> Optional[SupportCase]:
        # Try fetching by UUID primary key or by case_number
        stmt = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        res = await self.session.exec(stmt)
        return res.first()

    async def list_cases(
        self,
        customer_id: Optional[str] = None,
        provider_id: Optional[str] = None,
        assigned_agent_id: Optional[str] = None,
        task_id: Optional[str] = None,
        status: Optional[CaseStatus] = None,
        priority: Optional[CasePriority] = None,
        type: Optional[CaseType] = None,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[SupportCase], int]:
        stmt = select(SupportCase)
        count_stmt = select(func.count()).select_from(SupportCase)

        filters = []
        if customer_id:
            filters.append(col(SupportCase.customer_id) == customer_id)
        if provider_id:
            filters.append(col(SupportCase.provider_id) == provider_id)
        if assigned_agent_id:
            filters.append(col(SupportCase.assigned_agent_id) == assigned_agent_id)
        if task_id:
            filters.append(col(SupportCase.task_id) == task_id)
        if status:
            filters.append(col(SupportCase.status) == status)
        if priority:
            filters.append(col(SupportCase.priority) == priority)
        if type:
            filters.append(col(SupportCase.type) == type)
        if search:
            filters.append(
                or_(
                    col(SupportCase.subject).ilike(f"%{search}%"),
                    col(SupportCase.case_number).ilike(f"%{search}%"),
                )
            )

        if filters:
            stmt = stmt.where(and_(*filters))
            count_stmt = count_stmt.where(and_(*filters))

        total_res = await self.session.exec(count_stmt)
        total = total_res.one() or 0

        stmt = stmt.order_by(col(SupportCase.updated_at).desc()).limit(limit).offset(offset)
        res = await self.session.exec(stmt)
        return res.all(), total

    async def update_case(
        self,
        case_id: str,
        schema: SupportCaseUpdate,
        actor_id: str,
        actor_type: str,
    ) -> Optional[SupportCase]:
        case = await self.get_case(case_id)
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
        case = await self.get_case(case_id)
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
        case = await self.get_case(case_id)
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

    async def get_timeline(
        self,
        case_id: str,
        is_admin: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> List[TimelineItemResponse]:
        case = await self.get_case(case_id)
        if not case:
            return []

        # Query events
        stmt_events = select(CaseEvent).where(col(CaseEvent.case_id) == case.id)
        res_events = await self.session.exec(stmt_events)
        events = res_events.all()

        # Query messages
        stmt_msgs = select(CaseMessage).where(col(CaseMessage.case_id) == case.id)
        if not is_admin:
            stmt_msgs = stmt_msgs.where(col(CaseMessage.visibility) == MessageVisibility.PUBLIC)
        res_msgs = await self.session.exec(stmt_msgs)
        messages = res_msgs.all()

        items: List[TimelineItemResponse] = []
        for e in events:
            items.append(
                TimelineItemResponse(
                    id=e.id,
                    item_type="EVENT",
                    timestamp=e.created_at,
                    title=f"Event: {e.event_type.value}",
                    description=None,
                    actor_type=e.actor_type,
                    actor_id=e.actor_id,
                    metadata=e.event_metadata,
                )
            )
        for m in messages:
            items.append(
                TimelineItemResponse(
                    id=m.id,
                    item_type="MESSAGE",
                    timestamp=m.created_at,
                    title=f"Message ({m.sender_type.value})",
                    description=m.body,
                    actor_type=m.sender_type.value,
                    actor_id=m.sender_id,
                    metadata={"channel": m.channel.value, "visibility": m.visibility.value},
                )
            )

        items.sort(key=lambda x: x.timestamp)
        return items[offset : offset + limit]
