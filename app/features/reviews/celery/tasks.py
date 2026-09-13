from typing import Optional
from celery import shared_task
from sqlalchemy import literal, select as sa_select
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import func, select, col

from app.core.celery_database import celery_session_factory
from app.core.logging import logger
from app.core.models.reviews import TaskReview
from app.core.models.users import UserStats
from app.core.repository import QueryOptions, Repository
from app.core.services.logger_service import get_logger_service_manual
from app.core.utils.celery import run_async
from app.core.utils.datetime_helper import lagos_now
from app.core.utils.timer import Timer


@shared_task(name="reviews.sync_user_ratings")
def sync_user_ratings(user_id: str):
    """Recompute user_stats.average_ratings and total_ratings from all TaskReview records for this user."""
    logger.info(f"sync_user_ratings: user_id={user_id}")
    return run_async(_sync_user_ratings_async(user_id))




async def _sync_user_ratings_async(user_id: str) -> None:
    async with celery_session_factory() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()

        try:
            # Calculate both values inside PostgreSQL
            rating_stats = (
                select(
                    func.coalesce(
                        func.round(func.avg(TaskReview.rating), 2),
                        0.0,
                    ).label("average_ratings"),
                    func.count(col(TaskReview.id)).label("total_ratings"),
                )
                .where(TaskReview.reviewee_id == user_id)
                .subquery()
            )

            stmt = (
                insert(UserStats)
                .from_select(
                    ["user_id", "average_ratings", "total_ratings",
                     "created_at", "updated_at"],
                    sa_select(
                        literal(user_id),
                        rating_stats.c.average_ratings,
                        rating_stats.c.total_ratings,
                        func.now(),
                        func.now(),
                    ),
                )
                .on_conflict_do_update(
                    index_elements=[UserStats.user_id],
                    set_={
                        "average_ratings": rating_stats.c.average_ratings,
                        "total_ratings": rating_stats.c.total_ratings,
                        "updated_at": func.now(),
                    },
                )
            )

            await session.exec(stmt)
            await session.commit()

            await system_logger.metric(
                "sync_user_ratings",
                timer.stop(),
                source="celery.sync_user_ratings",
            )

        except Exception as e:
            await system_logger.error(
                f"sync_user_ratings Failed: {str(e)}",
                source="celery.sync_user_ratings",
            )
            raise
