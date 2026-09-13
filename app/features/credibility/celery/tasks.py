from typing import Optional
from celery import shared_task
from sqlmodel import func, select
from sqlalchemy.dialects.postgresql import insert
from app.core.celery_database import celery_session_factory
from app.core.logging import logger
from app.core.models.credibility import CredibilityLedgerEntry
from app.core.models.users import User, UserStats
from app.core.repository import QueryOptions, Repository
from app.core.services.logger_service import get_logger_service_manual
from app.core.utils.celery import run_async
from app.core.utils.datetime_helper import lagos_now
from app.core.utils.timer import Timer

_CREDIBILITY_MIN = 0.0
_CREDIBILITY_MAX = 100.0
_CREDIBILITY_SEED = 25.0


@shared_task(name="credibility.sync_user_credibility_score")
def sync_user_credibility_score(user_id: str):
    """Sum all credibility ledger deltas for a user and write back to user_stats.credibility_score."""
    logger.info(f"sync_user_credibility_score: user_id={user_id}")
    return run_async(_sync_user_credibility_score_async(user_id))

async def _sync_user_credibility_score_async(user_id: str) -> None:
    async with celery_session_factory() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()

        try:
            ledger_sum = (
                select(func.coalesce(func.sum(CredibilityLedgerEntry.delta), 0))
                .where(CredibilityLedgerEntry.user_id == user_id)
                .scalar_subquery()
            )

            raw_score = _CREDIBILITY_SEED + ledger_sum

            clamped_score = func.round(
                func.greatest(
                    _CREDIBILITY_MIN,
                    func.least(
                        _CREDIBILITY_MAX,
                        raw_score,
                    ),
                ),
                2,
            )

            stmt = (
                insert(UserStats)
                .values(
                    user_id=user_id,
                    credibility_score=clamped_score,
                    created_at=func.now(),
                    updated_at=func.now(),
                )
                .on_conflict_do_update(
                    index_elements=[UserStats.user_id],
                    set_={
                        "credibility_score": clamped_score,
                        "updated_at": func.now(),
                    },
                )
            )

            await session.exec(stmt)
            await session.commit()

            await system_logger.metric(
                "sync_user_credibility_score",
                timer.stop(),
                source="celery.sync_user_credibility_score",
            )

        except Exception as e:
            await system_logger.error(
                f"sync_user_credibility_score Failed: {str(e)}",
                source="celery.sync_user_credibility_score",
            )
            raise
