from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import Depends, HTTPException, status
from sqlmodel import col, select

from app.core.models.credibility import (
    CredibilityLedgerEntry,
    get_review_credibility_reason,
)
from app.core.models.reviews import TaskReview
from app.core.models.tasks import Task, TaskStatus
from app.core.models.users import User
from app.core.repository import GetRepository, Repository
from app.features.credibility.services import CredibilityService, get_credibility_service
from app.features.reviews.schemas import CreateReviewRequest
from app.features.reviews.celery.tasks import sync_user_ratings

_REVIEW_WINDOW_HOURS = 48


class ReviewService:
    def __init__(
        self,
        review_repo: Repository[TaskReview],
        task_repo: Repository[Task],
        user_repo: Repository[User],
        credibility_service: CredibilityService,
    ):
        self.review_repo = review_repo
        self.task_repo = task_repo
        self.user_repo = user_repo
        self.credibility_service = credibility_service

    async def submit_review(
        self,
        reviewer_id: str,
        schema: CreateReviewRequest,
    ) -> TaskReview:
        """Validate and persist a review. Inserts credibility ledger entry via CredibilityService."""
        task = await self.task_repo.get(schema.task_id)
        if not task:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

        if task.status != TaskStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reviews can only be submitted for completed tasks",
            )

        # Enforce review window
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_REVIEW_WINDOW_HOURS)
        if task.updated_at and task.updated_at.replace(tzinfo=timezone.utc) < cutoff:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Review window of {_REVIEW_WINDOW_HOURS} hours has expired",
            )

        # Determine reviewee_id: reviewer is customer → reviewee is provider, and vice versa
        reviewer = await self.user_repo.get(reviewer_id)
        if not reviewer:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reviewer not found")

        if reviewer_id == task.customer_id:
            reviewee_id = task.assigned_provider_id
        elif reviewer_id == task.assigned_provider_id:
            reviewee_id = task.customer_id
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You were not a participant in this task",
            )

        if not reviewee_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unable to determine reviewee for this task",
            )

        # Prevent duplicate reviews
        stmt_existing = select(TaskReview).where(
            TaskReview.task_id == schema.task_id,
            TaskReview.reviewer_id == reviewer_id,
        )
        existing = (await self.review_repo.execute(stmt_existing)).one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You have already submitted a review for this task",
            )

        review = TaskReview(
            task_id=schema.task_id,
            reviewer_id=reviewer_id,
            reviewee_id=reviewee_id,
            rating=schema.rating,
            comment=schema.comment,
            is_visible=False,
        )
        review = await self.review_repo.add(review)

        # Insert credibility entry via CredibilityService (which enqueues score sync Celery task)
        reason = get_review_credibility_reason(schema.rating)
        await self.credibility_service.add_credibility_entry(
            user_id=reviewee_id,
            reason=reason,
            task_id=schema.task_id,
            metadata_info={"rating": schema.rating, "reviewer_id": reviewer_id},
        )

        # Check double-blind: if both parties reviewed, make both visible
        await self._maybe_reveal_reviews(schema.task_id, task)

        # Sync ratings as soon as a review comes in
        # pyrefly: ignore [not-callable]
        sync_user_ratings.delay(reviewee_id)

        return review

    async def _maybe_reveal_reviews(self, task_id: str, task: Task) -> None:
        """Reveal reviews for a task if both parties have submitted."""
        stmt = select(TaskReview).where(TaskReview.task_id == task_id)
        reviews = list((await self.review_repo.execute(stmt)).all())

        reviewer_ids = {r.reviewer_id for r in reviews}
        both_submitted = (
            task.customer_id in reviewer_ids
            and task.assigned_provider_id in reviewer_ids
        )

        if both_submitted:
            
            for r in reviews:
                if not r.is_visible:
                    r.is_visible = True
                    await self.review_repo.add(r)

    async def get_reviews_for_task(self, task_id: str) -> List[TaskReview]:
        stmt = select(TaskReview).where(
            TaskReview.task_id == task_id,
            TaskReview.is_visible == True,  # noqa: E712
        )
        return list((await self.review_repo.execute(stmt)).all())

    async def get_reviews_for_user(
        self,
        user_id: str,
        page: int = 1,
        per_page: int = 20,
    ) -> List[TaskReview]:
        offset = (page - 1) * per_page
        stmt = (
            select(TaskReview)
            .where(
                TaskReview.reviewee_id == user_id,
                TaskReview.is_visible == True,  # noqa: E712
            )
            .order_by(col(TaskReview.created_at).desc())
            .offset(offset)
            .limit(per_page)
        )
        return list((await self.review_repo.execute(stmt)).all())

    async def get_credibility_ledger(
        self,
        user_id: str,
        page: int = 1,
        per_page: int = 20,
    ) -> List[CredibilityLedgerEntry]:
        return await self.credibility_service.get_user_ledger(user_id, page=page, per_page=per_page)


def get_review_service(
    review_repo: Repository[TaskReview] = Depends(GetRepository(TaskReview)),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    user_repo: Repository[User] = Depends(GetRepository(User)),
    credibility_service: CredibilityService = Depends(get_credibility_service),
) -> ReviewService:
    return ReviewService(
        review_repo=review_repo,
        task_repo=task_repo,
        user_repo=user_repo,
        credibility_service=credibility_service,
    )
