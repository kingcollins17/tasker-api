from typing import List, Optional, Tuple

from sqlmodel import col, select, or_
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.models.support import (
    CaseAssignment,
    CaseEvent,
    CaseEventType,
    SupportCase,
)
from app.core.utils.datetime_helper import lagos_now


class CaseAssignmentService:
    """Service managing support agent assignment, re-assignment, and assignment history."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def assign_case(
        self,
        case_id: str,
        agent_id: str,
        assigned_by: str,
        reason: Optional[str] = None,
    ) -> Tuple[Optional[SupportCase], Optional[CaseAssignment]]:
        stmt = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        res = await self.session.exec(stmt)
        case = res.first()
        if not case:
            return None, None

        now = lagos_now()
        previous_agent_id = case.assigned_agent_id

        # Mark existing active assignment as unassigned
        if previous_agent_id:
            stmt_active = select(CaseAssignment).where(
                col(CaseAssignment.case_id) == case.id,
                col(CaseAssignment.unassigned_at) == None,  # noqa: E711
            )
            res_active = await self.session.exec(stmt_active)
            active_assignments = res_active.all()
            for active in active_assignments:
                active.unassigned_at = now
                self.session.add(active)

        # Create new assignment
        assignment = CaseAssignment(
            case_id=case.id,
            agent_id=agent_id,
            assigned_by=assigned_by,
            assigned_at=now,
            reason=reason,
        )
        self.session.add(assignment)

        case.assigned_agent_id = agent_id
        case.updated_at = now
        self.session.add(case)

        event_type = CaseEventType.CASE_REASSIGNED if previous_agent_id else CaseEventType.CASE_ASSIGNED
        event = CaseEvent(
            case_id=case.id,
            event_type=event_type,
            actor_type="AGENT",
            actor_id=assigned_by,
            event_metadata={
                "previous_agent_id": previous_agent_id,
                "new_agent_id": agent_id,
                "reason": reason,
            },
            created_at=now,
        )
        self.session.add(event)

        await self.session.commit()
        await self.session.refresh(assignment)
        await self.session.refresh(case)
        return case, assignment

    async def get_assignment_history(self, case_id: str) -> List[CaseAssignment]:
        stmt_case = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        res_case = await self.session.exec(stmt_case)
        case = res_case.first()
        if not case:
            return []

        stmt = select(CaseAssignment).where(col(CaseAssignment.case_id) == case.id).order_by(col(CaseAssignment.assigned_at).asc())
        res = await self.session.exec(stmt)
        return res.all()
