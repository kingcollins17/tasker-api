from typing import Optional, Tuple

from sqlmodel import col, select, or_
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.models.support import (
    CaseEvent,
    CaseEventType,
    CaseResolution,
    CaseStatus,
    SupportCase,
)
from app.core.utils.datetime_helper import lagos_now
from app.features.support.schemas import CaseResolutionCreate


class CaseResolutionService:
    """Service recording and querying final support case and dispute resolutions."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def resolve_case(
        self,
        case_id: str,
        agent_id: str,
        schema: CaseResolutionCreate,
    ) -> Tuple[Optional[SupportCase], Optional[CaseResolution]]:
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
        case.status = CaseStatus.RESOLVED
        case.resolved_at = now
        case.updated_at = now
        self.session.add(case)

        resolution = CaseResolution(
            case_id=case.id,
            decision=schema.decision,
            reason=schema.reason,
            resolved_by=agent_id,
            created_at=now,
        )
        self.session.add(resolution)

        event = CaseEvent(
            case_id=case.id,
            event_type=CaseEventType.CASE_RESOLVED,
            actor_type="AGENT",
            actor_id=agent_id,
            event_metadata={
                "decision": schema.decision,
                "reason": schema.reason,
                "resolution_id": resolution.id,
            },
            created_at=now,
        )
        self.session.add(event)

        await self.session.commit()
        await self.session.refresh(resolution)
        await self.session.refresh(case)
        return case, resolution

    async def get_resolution(self, case_id: str) -> Optional[CaseResolution]:
        stmt_case = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        res_case = await self.session.exec(stmt_case)
        case = res_case.first()
        if not case:
            return None

        stmt = select(CaseResolution).where(col(CaseResolution.case_id) == case.id)
        res = await self.session.exec(stmt)
        return res.first()
