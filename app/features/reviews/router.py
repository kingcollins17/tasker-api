from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.api_response import BaseAPIResponse
from app.core.deps.auth import GetCurrentUser
from app.core.error_handler import AppErrorHandler
from app.core.models.users import User
from app.features.reviews.celery.tasks import sync_user_ratings
from app.features.reviews.schemas import (
    CreateReviewRequest,
    CredibilityLedgerEntryResponse,
    ReviewResponse,
)
from app.features.reviews.services import ReviewService, get_review_service

router = APIRouter(prefix="/reviews", tags=["Reviews & Credibility"])


def _review_to_response(r) -> ReviewResponse:
    return ReviewResponse(
        id=r.id,
        task_id=r.task_id,
        reviewer_id=r.reviewer_id,
        reviewee_id=r.reviewee_id,
        rating=r.rating,
        comment=r.comment,
        is_visible=r.is_visible,
        created_at=r.created_at.isoformat() if r.created_at else None,
    )


def _ledger_to_response(e) -> CredibilityLedgerEntryResponse:
    return CredibilityLedgerEntryResponse(
        id=e.id,
        user_id=e.user_id,
        delta=e.delta,
        reason=e.reason.value if e.reason else None,
        task_id=e.task_id,
        created_at=e.created_at.isoformat() if e.created_at else None,
    )


@router.post("", response_model=BaseAPIResponse[ReviewResponse], status_code=status.HTTP_201_CREATED)
async def submit_review(
    payload: CreateReviewRequest,
    current_user: User = Depends(GetCurrentUser()),
    service: ReviewService = Depends(get_review_service),
):
    """Submit a star rating and optional comment for a completed task."""
    try:
        review = await service.submit_review(reviewer_id=current_user.id, schema=payload)

        # Enqueue async post-review metric recalculations
        # pyrefly: ignore [not-callable]
        sync_user_ratings.delay(review.reviewee_id)

        return BaseAPIResponse(
            data=_review_to_response(review),
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit review",
        )


@router.get(
    "/task/{task_id}",
    response_model=BaseAPIResponse[List[ReviewResponse]],
)
async def get_task_reviews(
    task_id: str,
    current_user: User = Depends(GetCurrentUser()),
    service: ReviewService = Depends(get_review_service),
):
    """Get all visible reviews for a specific task."""
    try:
        reviews = await service.get_reviews_for_task(task_id)
        return BaseAPIResponse(
            data=[_review_to_response(r) for r in reviews],
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch reviews",
        )


@router.get(
    "/user/{user_id}",
    response_model=BaseAPIResponse[List[ReviewResponse]],
)
async def get_user_reviews(
    user_id: str,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(GetCurrentUser()),
    service: ReviewService = Depends(get_review_service),
):
    """Get all visible reviews received by a user, paginated."""
    try:
        reviews = await service.get_reviews_for_user(user_id, page=page, per_page=per_page)
        return BaseAPIResponse(
            data=[_review_to_response(r) for r in reviews],
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch user reviews",
        )


@router.get(
    "/credibility/ledger",
    response_model=BaseAPIResponse[List[CredibilityLedgerEntryResponse]],
)
async def get_my_credibility_ledger(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(GetCurrentUser()),
    service: ReviewService = Depends(get_review_service),
):
    """View your own credibility score ledger entries (paginated)."""
    try:
        entries = await service.get_credibility_ledger(
            current_user.id, page=page, per_page=per_page
        )
        return BaseAPIResponse(
            data=[_ledger_to_response(e) for e in entries],
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch credibility ledger",
        )
