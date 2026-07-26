from app.core.utils.timer import Timer
from app.core.services.logger_service import get_logger_service_manual
from typing import Optional

from celery import shared_task
from sqlmodel import func, select

from app.core.database import async_session_maker
from app.core.logging import logger
from app.core.models.reviews import TaskReview
from app.core.models.users import User
from app.core.repository import Repository
from app.core.utils.celery import run_async


@shared_task(name="reviews.sync_user_ratings")
def sync_user_ratings(user_id: str):
    """Recompute users.average_ratings from all visible TaskReview records for this user."""
    logger.info(f"sync_user_ratings: user_id={user_id}")
    return run_async(_sync_user_ratings_async(user_id))


async def _sync_user_ratings_async(user_id: str) -> None:
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            user_repo = Repository(User, session)
            review_repo = Repository(TaskReview, session)

            stmt = select(func.avg(TaskReview.rating)).where(
                TaskReview.reviewee_id == user_id,
                TaskReview.is_visible == True,  # noqa: E712
            )
            result = await review_repo.execute(stmt)
            avg_rating: Optional[float] = result.scalar_one_or_none()

            user = await user_repo.get(user_id)
            if user:
                user.average_ratings = round(avg_rating or 0.0, 2)
                await user_repo.add(user)
                logger.info(
                    f"Updated user {user_id} average_ratings → {user.average_ratings}"
                )
            await system_logger.metric(
                "sync_user_ratings", timer.stop(), source="celery.sync_user_ratings"
            )
        except Exception as e:
            await system_logger.error(
                f"sync_user_ratings Failed: {str(e)}", source="celery.sync_user_ratings"
            )
            raise e
