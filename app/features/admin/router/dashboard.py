from typing import Dict
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.api_response import BaseAPIResponse
from app.core.database import get_session
from app.core.deps.auth import GetCurrentAdmin
from app.core.error_handler import AppErrorHandler
from app.core.models.admins import AdminUser
from app.core.models.payments import PayoutQueue, PayoutStatus
from app.core.models.tasks import Task, TaskStatus
from app.core.models.transactions import Transaction
from app.core.models.users import User, UserType, KYCDocument, KYCStatus, VerificationStatus
from app.core.models.vetting import ProviderGuarantor, ProviderInterview, InterviewStatus
from app.core.services.cache import CacheService, get_cache_service
from app.features.admin.schemas import (
    AdminDashboardOverviewResponse,
    AdminUserStatsResponse,
    AdminKYCStatsResponse,
    AdminGuarantorStatsResponse,
    AdminInterviewStatsResponse,
    AdminTaskStatsResponse,
)

router = APIRouter(prefix="/dashboard", tags=["Admin Dashboard"])


@router.get(
    "/overview",
    response_model=BaseAPIResponse[AdminDashboardOverviewResponse],
    summary="Get admin dashboard overview metrics",
)
async def get_dashboard_overview(
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
    cs: CacheService = Depends(get_cache_service),
):
    """Fetches high-level admin dashboard overview metrics including total users, tasks, revenue, and completed payouts."""
    try:
        cache_key = "admin:dashboard:overview"
        cached_data = await cs.get_json(cache_key)
        if cached_data:
            return BaseAPIResponse.success_response(
                data=AdminDashboardOverviewResponse(**cached_data),
                message="Dashboard overview retrieved successfully.",
            )

        total_users_stmt = select(func.count(col(User.id)))
        total_users = (await session.exec(total_users_stmt)).one() or 0

        total_customers_stmt = select(func.count(col(User.id))).where(col(User.type) == UserType.CUSTOMER)
        total_customers = (await session.exec(total_customers_stmt)).one() or 0

        total_providers_stmt = select(func.count(col(User.id))).where(col(User.type) == UserType.PROVIDER)
        total_providers = (await session.exec(total_providers_stmt)).one() or 0

        total_tasks_stmt = select(func.count(col(Task.id)))
        total_tasks = (await session.exec(total_tasks_stmt)).one() or 0

        total_completed_tasks_stmt = select(func.count(col(Task.id))).where(col(Task.status) == TaskStatus.COMPLETED)
        total_completed_tasks = (await session.exec(total_completed_tasks_stmt)).one() or 0

        total_in_progress_tasks_stmt = select(func.count(col(Task.id))).where(col(Task.status) == TaskStatus.IN_PROGRESS)
        total_in_progress_tasks = (await session.exec(total_in_progress_tasks_stmt)).one() or 0

        total_open_tasks_stmt = select(func.count(col(Task.id))).where(
            col(Task.status).in_([TaskStatus.OPEN, TaskStatus.SEARCHING, TaskStatus.ASSIGNED])
        )
        total_open_tasks = (await session.exec(total_open_tasks_stmt)).one() or 0

        total_cancelled_tasks_stmt = select(func.count(col(Task.id))).where(col(Task.status) == TaskStatus.CANCELLED)
        total_cancelled_tasks = (await session.exec(total_cancelled_tasks_stmt)).one() or 0

        total_revenue_stmt = select(func.coalesce(func.sum(col(Transaction.amount)), 0.0))
        total_revenue_amount = float((await session.exec(total_revenue_stmt)).one() or 0.0)

        total_payouts_stmt = select(func.coalesce(func.sum(col(PayoutQueue.payout_amount)), 0.0)).where(
            col(PayoutQueue.status) == PayoutStatus.COMPLETED
        )
        total_processed_payouts_amount = float((await session.exec(total_payouts_stmt)).one() or 0.0)

        overview = AdminDashboardOverviewResponse(
            total_users=total_users,
            total_customers=total_customers,
            total_providers=total_providers,
            total_tasks=total_tasks,
            total_completed_tasks=total_completed_tasks,
            total_in_progress_tasks=total_in_progress_tasks,
            total_open_tasks=total_open_tasks,
            total_cancelled_tasks=total_cancelled_tasks,
            total_revenue_amount=round(total_revenue_amount, 2),
            total_processed_payouts_amount=round(total_processed_payouts_amount, 2),
        )

        await cs.set_json(cache_key, overview.model_dump(mode="json"), expire=300 * 2)

        return BaseAPIResponse.success_response(
            data=overview,
            message="Dashboard overview retrieved successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred fetching dashboard overview.",
        )


@router.get(
    "/user-stats",
    response_model=BaseAPIResponse[AdminUserStatsResponse],
    summary="Get platform user statistics",
)
async def get_user_stats(
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
    cs: CacheService = Depends(get_cache_service),
):
    """Fetches platform user statistics (total, active, inactive, customers, providers) in one DB query using GROUP BY."""
    try:
        cache_key = "admin:dashboard:user-stats"
        cached_data = await cs.get_json(cache_key)
        if cached_data:
            return BaseAPIResponse.success_response(
                data=AdminUserStatsResponse(**cached_data),
                message="User statistics retrieved successfully.",
            )

        stmt = select(User.type, User.is_active, func.count(col(User.id))).group_by(col(User.type), col(User.is_active))
        results = (await session.exec(stmt)).all()

        total_users = 0
        total_active = 0
        total_inactive = 0
        total_customers = 0
        total_providers = 0

        for user_type, is_active, count in results:
            total_users += count
            if is_active:
                total_active += count
            else:
                total_inactive += count

            if user_type == UserType.CUSTOMER:
                total_customers += count
            elif user_type == UserType.PROVIDER:
                total_providers += count

        stats = AdminUserStatsResponse(
            total_users=total_users,
            total_active=total_active,
            total_inactive=total_inactive,
            total_customers=total_customers,
            total_providers=total_providers,
        )

        await cs.set_json(cache_key, stats.model_dump(mode="json"), expire=300)

        return BaseAPIResponse.success_response(
            data=stats,
            message="User statistics retrieved successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred fetching user statistics.",
        )


@router.get(
    "/kyc-stats",
    response_model=BaseAPIResponse[AdminKYCStatsResponse],
    summary="Get platform KYC verification statistics",
)
async def get_kyc_stats(
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
    cs: CacheService = Depends(get_cache_service),
):
    """Fetches platform KYC verification statistics in one DB query using GROUP BY."""
    try:
        cache_key = "admin:dashboard:kyc-stats"
        cached_data = await cs.get_json(cache_key)
        if cached_data:
            return BaseAPIResponse.success_response(
                data=AdminKYCStatsResponse(**cached_data),
                message="KYC statistics retrieved successfully.",
            )

        stmt = select(KYCDocument.status, func.count(col(KYCDocument.id))).group_by(col(KYCDocument.status))
        results = (await session.exec(stmt)).all()

        counts = {kyc_status: count for kyc_status, count in results}

        total_documents = sum(counts.values())
        total_verified = counts.get(KYCStatus.VERIFIED, 0)
        total_rejected = counts.get(KYCStatus.FAILED, 0)
        total_submitted = counts.get(KYCStatus.SUBMITTED, 0)
        total_under_review = counts.get(KYCStatus.UNDER_REVIEW, 0)
        total_pending = counts.get(KYCStatus.PENDING_SUBMISSION, 0) + total_submitted + total_under_review

        stats = AdminKYCStatsResponse(
            total_documents=total_documents,
            total_verified=total_verified,
            total_rejected=total_rejected,
            total_pending=total_pending,
            total_submitted=total_submitted,
            total_under_review=total_under_review,
        )

        await cs.set_json(cache_key, stats.model_dump(mode="json"), expire=300)

        return BaseAPIResponse.success_response(
            data=stats,
            message="KYC statistics retrieved successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred fetching KYC statistics.",
        )


@router.get(
    "/guarantor-stats",
    response_model=BaseAPIResponse[AdminGuarantorStatsResponse],
    summary="Get platform guarantor verification statistics",
)
async def get_guarantor_stats(
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
    cs: CacheService = Depends(get_cache_service),
):
    """Fetches platform guarantor verification statistics in one DB query using GROUP BY."""
    try:
        cache_key = "admin:dashboard:guarantor-stats"
        cached_data = await cs.get_json(cache_key)
        if cached_data:
            return BaseAPIResponse.success_response(
                data=AdminGuarantorStatsResponse(**cached_data),
                message="Guarantor statistics retrieved successfully.",
            )

        stmt = select(ProviderGuarantor.status, func.count(col(ProviderGuarantor.id))).group_by(col(ProviderGuarantor.status))
        results = (await session.exec(stmt)).all()

        counts = {status_val: count for status_val, count in results}

        total_guarantors = sum(counts.values())
        total_passed = counts.get(VerificationStatus.PASSED, 0)
        total_failed = counts.get(VerificationStatus.FAILED, 0)
        total_pending = counts.get(VerificationStatus.PENDING, 0)
        total_under_review = counts.get(VerificationStatus.UNDER_REVIEW, 0)

        stats = AdminGuarantorStatsResponse(
            total_guarantors=total_guarantors,
            total_passed=total_passed,
            total_failed=total_failed,
            total_pending=total_pending,
            total_under_review=total_under_review,
        )

        await cs.set_json(cache_key, stats.model_dump(mode="json"), expire=300)

        return BaseAPIResponse.success_response(
            data=stats,
            message="Guarantor statistics retrieved successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred fetching guarantor statistics.",
        )


@router.get(
    "/interview-stats",
    response_model=BaseAPIResponse[AdminInterviewStatsResponse],
    summary="Get platform interview statistics",
)
async def get_interview_stats(
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
    cs: CacheService = Depends(get_cache_service),
):
    """Fetches platform interview statistics in one DB query using GROUP BY."""
    try:
        cache_key = "admin:dashboard:interview-stats"
        cached_data = await cs.get_json(cache_key)
        if cached_data:
            return BaseAPIResponse.success_response(
                data=AdminInterviewStatsResponse(**cached_data),
                message="Interview statistics retrieved successfully.",
            )

        stmt = select(ProviderInterview.status, func.count(col(ProviderInterview.id))).group_by(col(ProviderInterview.status))
        results = (await session.exec(stmt)).all()

        counts = {status_val: count for status_val, count in results}

        total_interviews = sum(counts.values())
        total_scheduled = counts.get(InterviewStatus.SCHEDULED, 0)
        total_passed = counts.get(InterviewStatus.PASSED, 0)
        total_failed = counts.get(InterviewStatus.FAILED, 0)
        total_cancelled = counts.get(InterviewStatus.CANCELLED, 0)
        total_rescheduled = counts.get(InterviewStatus.RESCHEDULED, 0)

        stats = AdminInterviewStatsResponse(
            total_interviews=total_interviews,
            total_scheduled=total_scheduled,
            total_passed=total_passed,
            total_failed=total_failed,
            total_cancelled=total_cancelled,
            total_rescheduled=total_rescheduled,
        )

        await cs.set_json(cache_key, stats.model_dump(mode="json"), expire=300)

        return BaseAPIResponse.success_response(
            data=stats,
            message="Interview statistics retrieved successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred fetching interview statistics.",
        )


@router.get(
    "/task-stats",
    response_model=BaseAPIResponse[AdminTaskStatsResponse],
    summary="Get platform task statistics",
)
@router.get(
    "/tasks-stats",
    response_model=BaseAPIResponse[AdminTaskStatsResponse],
    summary="Get platform task statistics",
    include_in_schema=False,
)
async def get_task_stats(
    current_admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
    cs: CacheService = Depends(get_cache_service),
):
    """Fetches platform task statistics grouped by status (draft, open, searching, assigned, in progress, completed, cancelled, etc.)."""
    try:
        cache_key = "admin:dashboard:task-stats"
        cached_data = await cs.get_json(cache_key)
        if cached_data:
            return BaseAPIResponse.success_response(
                data=AdminTaskStatsResponse(**cached_data),
                message="Task statistics retrieved successfully.",
            )

        stmt = select(Task.status, func.count(col(Task.id))).group_by(col(Task.status))
        results = (await session.exec(stmt)).all()

        counts: Dict[str, int] = {}
        for status_val, count in results:
            key = status_val.value if hasattr(status_val, "value") else str(status_val)
            counts[key] = count

        total_tasks = sum(counts.values())
        total_draft = counts.get(TaskStatus.DRAFT.value, 0)
        total_under_review = counts.get(TaskStatus.UNDER_REVIEW.value, 0)
        total_open = counts.get(TaskStatus.OPEN.value, 0)
        total_searching = counts.get(TaskStatus.SEARCHING.value, 0)
        total_assigned = counts.get(TaskStatus.ASSIGNED.value, 0)
        total_in_progress = counts.get(TaskStatus.IN_PROGRESS.value, 0)
        total_completed = counts.get(TaskStatus.COMPLETED.value, 0)
        total_cancelled = counts.get(TaskStatus.CANCELLED.value, 0)
        total_no_match = counts.get(TaskStatus.NO_MATCH.value, 0)

        by_status = {status_item.value: counts.get(status_item.value, 0) for status_item in TaskStatus}

        stats = AdminTaskStatsResponse(
            total_tasks=total_tasks,
            total_draft=total_draft,
            total_under_review=total_under_review,
            total_open=total_open,
            total_searching=total_searching,
            total_assigned=total_assigned,
            total_in_progress=total_in_progress,
            total_completed=total_completed,
            total_cancelled=total_cancelled,
            total_no_match=total_no_match,
            by_status=by_status,
        )

        await cs.set_json(cache_key, stats.model_dump(mode="json"), expire=300)

        return BaseAPIResponse.success_response(
            data=stats,
            message="Task statistics retrieved successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred fetching task statistics.",
        )

