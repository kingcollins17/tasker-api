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
from app.core.models.users import User, UserType
from app.core.services.cache import CacheService, get_cache_service
from app.features.admin.schemas import AdminDashboardOverviewResponse

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
