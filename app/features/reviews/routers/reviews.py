from app.core.schemas.tasks import TaskListResponse, PendingReviewTaskResponse
from app.core.schemas.users import MinimalCustomerResponse, MinimalProviderResponse
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.deps.auth import GetCurrentUser
from app.core.error_handler import AppErrorHandler
from app.core.models.reviews import TaskReview
from app.core.models.tasks import PaymentStatus, Task, TaskStatus
from app.core.models.users import User
from app.core.repository import GetRepository, Repository
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
    response_model=BaseAPIResponse[PaginatedData[ReviewResponse]],
)
async def get_task_reviews(
    task_id: str,
    current_user: User = Depends(GetCurrentUser()),
    service: ReviewService = Depends(get_review_service),
):
    """Get all visible reviews for a specific task."""
    try:
        reviews = await service.get_reviews_for_task(task_id)
        data = [_review_to_response(r) for r in reviews]
        return BaseAPIResponse(
            data=PaginatedData(items=data, total=len(data), page=1, per_page=len(data) if data else 20),
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
    "/pending/customer",
    response_model=BaseAPIResponse[PaginatedData[PendingReviewTaskResponse]],
)
async def get_pending_customer_reviews(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(GetCurrentUser()),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
):
    """Fetch completed and paid tasks that the customer hasn't reviewed."""
    try:
        offset = (page - 1) * per_page
        
        customer_reviewed = select(TaskReview.id).where(
            col(TaskReview.task_id) == col(Task.id),
            col(TaskReview.reviewer_id) == current_user.id
        ).exists()

        base_where = [
            col(Task.customer_id) == current_user.id,
            col(Task.status) == TaskStatus.COMPLETED,
            col(Task.payment_status).in_([PaymentStatus.PAID, PaymentStatus.CASH_PAID]),
            ~customer_reviewed,
        ]

        count_stmt = select(func.count(col(Task.id))).where(*base_where)
        total = (await task_repo.execute(count_stmt)).one_or_none() or 0

        stmt = (
            select(Task, User)
            .outerjoin(User, col(Task.assigned_provider_id) == col(User.id))
            .where(*base_where)
            .options(selectinload(User.provider_profile))
            .order_by(col(Task.created_at).desc())
            .offset(offset)
            .limit(per_page)
        )
        results = (await task_repo.execute(stmt)).unique().all()
        
        data = []
        for task, provider in results:
            task_dict = task.model_dump()
            if provider:
                task_dict["provider"] = MinimalProviderResponse.from_user(provider)
            data.append(PendingReviewTaskResponse(**task_dict))

        return BaseAPIResponse(
            data=PaginatedData(items=data, total=total, page=page, per_page=per_page),
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch pending customer reviews",
        )


@router.get(
    "/pending/provider",
    response_model=BaseAPIResponse[PaginatedData[PendingReviewTaskResponse]],
)
async def get_pending_provider_reviews(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(GetCurrentUser()),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
):
    """Fetch completed and paid tasks that the provider hasn't reviewed."""
    try:
        offset = (page - 1) * per_page
        
        provider_reviewed = select(TaskReview.id).where(
            col(TaskReview.task_id) == col(Task.id),
            col(TaskReview.reviewer_id) == current_user.id
        ).exists()

        base_where = [
            col(Task.assigned_provider_id) == current_user.id,
            col(Task.status) == TaskStatus.COMPLETED,
            col(Task.payment_status).in_([PaymentStatus.PAID, PaymentStatus.CASH_PAID]),
            ~provider_reviewed,
        ]

        count_stmt = select(func.count(col(Task.id))).where(*base_where)
        total = (await task_repo.execute(count_stmt)).one_or_none() or 0

        stmt = (
            select(Task, User)
            .outerjoin(User, col(Task.customer_id) == col(User.id))
            .where(*base_where)
            .options(selectinload(User.customer_profile))
            .order_by(col(Task.created_at).desc())
            .offset(offset)
            .limit(per_page)
        )
        results = (await task_repo.execute(stmt)).unique().all()
        
        data = []
        for task, customer in results:
            task_dict = task.model_dump()
            if customer:
                task_dict["customer"] = MinimalCustomerResponse.from_user(customer)
            data.append(PendingReviewTaskResponse(**task_dict))

        return BaseAPIResponse(
            data=PaginatedData(items=data, total=total, page=page, per_page=per_page),
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch pending provider reviews",
        )

@router.get(
    "/user",
    response_model=BaseAPIResponse[PaginatedData[ReviewResponse]],
)
async def get_user_reviews(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(GetCurrentUser()),
    service: ReviewService = Depends(get_review_service),
):
    """Get all visible reviews received by the currently authenticated user, paginated."""
    try:
        reviews = await service.get_reviews_for_user(current_user.id, page=page, per_page=per_page)
        data = [_review_to_response(r) for r in reviews]
        return BaseAPIResponse(
            data=PaginatedData(items=data, page=page, per_page=per_page),
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
    response_model=BaseAPIResponse[PaginatedData[CredibilityLedgerEntryResponse]],
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
        data = [_ledger_to_response(e) for e in entries]
        return BaseAPIResponse(
            data=PaginatedData(items=data, page=page, per_page=per_page),
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch credibility ledger",
        )
