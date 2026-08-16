from sqlmodel.ext.asyncio.session import AsyncSession
from typing import List, Optional

from fastapi import Depends
from sqlmodel import col, select

from app.core.logging import logger
from app.core.models.credibility import (
    CREDIBILITY_DELTAS,
    CredibilityLedgerEntry,
    CredibilityReason,
)
from app.core.repository import GetRepository, Repository
from app.features.credibility.celery.tasks import sync_user_credibility_score


class CredibilityService:
    """Service handling credibility ledger insertions and score synchronization triggers."""

    def __init__(self, ledger_repo: Repository[CredibilityLedgerEntry]):
        self.ledger_repo = ledger_repo

    async def add(
        self,
        user_id: str,
        reason: CredibilityReason,
        task_id: Optional[str] = None,
        metadata_info: Optional[dict] = None,
    ) -> Optional[CredibilityLedgerEntry]:
        """Insert a credibility ledger entry and enqueue Celery task to recalculate user credibility score."""
        delta = CREDIBILITY_DELTAS.get(reason, 0.0)
        if delta == 0.0:
            logger.info(f"Skipping credibility entry for user {user_id}: reason={reason} has zero delta")
            return None

        entry = CredibilityLedgerEntry(
            user_id=user_id,
            delta=delta,
            reason=reason,
            task_id=task_id,
            metadata_info=metadata_info,
        )
        entry = await self.ledger_repo.add(entry)
        logger.info(
            f"Inserted credibility entry for user {user_id}: reason={reason}, delta={delta:+.1f}"
        )

        # Trigger background Celery task to sync user.credibility_score
        # pyrefly: ignore [not-callable]
        sync_user_credibility_score.delay(user_id)

        return entry

    async def get_user_ledger(
        self,
        user_id: str,
        page: int = 1,
        per_page: int = 20,
    ) -> List[CredibilityLedgerEntry]:
        """Fetch paginated credibility ledger entries for a user."""
        offset = (page - 1) * per_page
        stmt = (
            select(CredibilityLedgerEntry)
            .where(CredibilityLedgerEntry.user_id == user_id)
            .order_by(col(CredibilityLedgerEntry.created_at).desc())
            .offset(offset)
            .limit(per_page)
        )
        result = await self.ledger_repo.execute(stmt)
        return list(result.all())


def get_credibility_service(
    ledger_repo: Repository[CredibilityLedgerEntry] = Depends(
        GetRepository(CredibilityLedgerEntry)
    ),
) -> CredibilityService:
    return CredibilityService(ledger_repo=ledger_repo)

def get_credibility_service_manual(session: AsyncSession):
    return CredibilityService(Repository(CredibilityLedgerEntry, session))