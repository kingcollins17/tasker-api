from sqlmodel import select, col
from sqlalchemy import func
from typing import Any
from app.core.models.users import User, ProviderProfile, UserLocation
from app.core.models.services import ProviderServiceLink
from app.core.models.tasks import Task, TaskLocation


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
            .where(col(User.region_id) == task.region_id)
            .where(
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
