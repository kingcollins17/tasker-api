from sqlmodel import select, col
from sqlalchemy import func
from typing import Any, List, Optional
from app.core.models.users import User, ProviderProfile, UserLocation
from app.core.models.services import ProviderServiceLink
from app.core.models.tasks import Task, TaskLocation, TaskStatus, TaskBid


class TaskQueries:
    @staticmethod
    def get_providers_near_task_query(
        task: Task,
        task_loc: TaskLocation,
        radius_km: float,
        select_ids_only: bool = False,
    ) -> Any:
        """Builds a SQLModel query statement to fetch providers near a task."""
        distance_m = radius_km * 1000

        base_select = select(User.id) if select_ids_only else select(User)

        stmt = (
            base_select.join(ProviderProfile, col(ProviderProfile.user_id) == User.id)
            .join(
                ProviderServiceLink,
                col(ProviderServiceLink.provider_id) == ProviderProfile.user_id,
            )
            .join(UserLocation, col(UserLocation.user_id) == User.id)
            .where(col(ProviderServiceLink.service_id) == task.service_id)
        )

        if task.region_id:
            stmt = stmt.where(col(User.region_id) == task.region_id)

        stmt = (
            stmt.where(
                func.ST_DistanceSphere(
                    UserLocation.last_known_location, task_loc.geography_point
                )
                <= distance_m
            )
            .order_by(
                col(User.average_ratings).desc(), col(User.credibility_score).desc()
            )
            .limit(100)
        )
        return stmt

    @staticmethod
    def get_customer_tasks_with_bid_counts_query(
        customer_id: str,
        statuses: List[TaskStatus],
        category_id: Optional[str] = None,
        service_id: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "scheduled_start_at",
        sort_desc: bool = True,
    ) -> Any:
        statement = (
            select(Task, func.count(TaskBid.id).label("bids_count"))
            .outerjoin(TaskBid, Task.id == TaskBid.task_id)
            .where(Task.customer_id == customer_id)
            .group_by(Task.id)
        )
        count_statement = (
            select(func.count())
            .select_from(Task)
            .where(Task.customer_id == customer_id)
        )

        if statuses:
            statement = statement.where(Task.status.in_(statuses))
            count_statement = count_statement.where(Task.status.in_(statuses))

        if category_id:
            statement = statement.where(Task.category_id == category_id)
            count_statement = count_statement.where(Task.category_id == category_id)

        if service_id:
            statement = statement.where(Task.service_id == service_id)
            count_statement = count_statement.where(Task.service_id == service_id)

        if search:
            search_pattern = f"%{search}%"
            search_filter = (col(Task.title).ilike(search_pattern)) | (
                col(Task.description).ilike(search_pattern)
            )
            statement = statement.where(search_filter)
            count_statement = count_statement.where(search_filter)

        if sort_by and hasattr(Task, sort_by):
            sort_col = getattr(Task, sort_by)
            statement = statement.order_by(sort_col.desc() if sort_desc else sort_col)

            if sort_by != "updated_at":
                statement = statement.order_by(Task.updated_at.desc())

        return statement, count_statement
