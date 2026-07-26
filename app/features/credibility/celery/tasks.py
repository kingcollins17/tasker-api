from app.core.utils.timer import Timer
from app.core.services.logger_service import get_logger_service_manual
from typing import Optional

from celery import shared_task
from sqlmodel import func, select

from app.core.database import async_session_maker
from app.core.logging import logger
from app.core.models.credibility import CredibilityLedgerEntry
from app.core.models.users import User
from app.core.repository import Repository
from app.core.utils.celery import run_async

_CREDIBILITY_MIN = 0.0
_CREDIBILITY_MAX = 100.0
_CREDIBILITY_SEED = 25.0


@shared_task(name="credibility.sync_user_credibility_score")
def sync_user_credibility_score(user_id: str):
    """Sum all credibility ledger deltas for a user and write back to users.credibility_score."""
    logger.info(f"sync_user_credibility_score: user_id={user_id}")
    return run_async(_sync_user_credibility_score_async(user_id))


async def _sync_user_credibility_score_async(user_id: str) -> None:
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            user_repo = Repository(User, session)
            ledger_repo = Repository(CredibilityLedgerEntry, session)

            stmt = select(func.sum(CredibilityLedgerEntry.delta)).where(
                CredibilityLedgerEntry.user_id == user_id,
            )
            result = await ledger_repo.execute(stmt)
            total_delta: Optional[float] = result.scalar_one_or_none()

            raw_score = _CREDIBILITY_SEED + (total_delta or 0.0)
            clamped = max(_CREDIBILITY_MIN, min(_CREDIBILITY_MAX, raw_score))

            user = await user_repo.get(user_id)
            if user:
                user.credibility_score = round(clamped, 2)
                await user_repo.add(user)
                logger.info(
                    f"Updated user {user_id} credibility_score → {user.credibility_score} "
                    f"(seed={_CREDIBILITY_SEED}, total_delta={total_delta})"
                )
            await system_logger.metric('sync_user_credibility_score', timer.stop(), source='celery.sync_user_credibility_score')
        except Exception as e:
            await system_logger.error(f'sync_user_credibility_score Failed: {str(e)}', source='celery.sync_user_credibility_score')
            raise e
