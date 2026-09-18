from typing import Optional, Tuple

from fastapi import Depends
from sqlmodel import col, select, update, or_
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
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
            stmt_unassign = (
                update(CaseAssignment)
                .where(
                    col(CaseAssignment.case_id) == case.id,
                    col(CaseAssignment.unassigned_at) == None,  # noqa: E711
                )
                .values(unassigned_at=now)
            )
            await self.session.exec(stmt_unassign)

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


def get_case_assignment_service(session: AsyncSession = Depends(get_session)) -> CaseAssignmentService:
    return CaseAssignmentService(session)
