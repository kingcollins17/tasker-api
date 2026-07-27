import math
import random
from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import Depends, HTTPException, status
from geoalchemy2 import Geography
from sqlalchemy import cast
from sqlalchemy.orm import contains_eager
from sqlmodel import col, desc, func, select

from app.core.models.notifications import (
    NotificationChannel,
    NotificationPriority,
    NotificationType,
)
from app.core.models.services import PricingRule, Service, ServiceCategory
from app.core.models.tasks import (
    DispatchAttemptStatus,
    LocationType,
    Task,
    TaskAssignment,
    TaskAssignmentStatus,
    TaskAttachment,
    TaskDispatchAttempt,
    TaskLocation,
    TaskStatus,
    TaskStatusHistory,
)
from app.core.models.transactions import Transaction, TransactionStatus, TransactionType
from app.core.models.users import ProviderProfile, User, UserLocation, UserType
from app.core.queries.task_queries import TaskQueries
from app.core.repository import GetRepository, QueryOptions, Repository
from app.core.services.payment import (
    PaymentGateway,
    PaymentInitializationResponse,
    get_paystack_gateway,
)
from app.core.utils.datetime_helper import lagos_now
from app.core.utils.geo import calculate_locations_distance
from app.features.notifications.schemas import CreateNotification
from app.features.notifications.services import (
    NotificationService,
    get_notification_service,
)
from app.features.services.pricing_engine import (
    PricingBreakdown,
    PricingCalculationRequest,
    PricingEngine,
    get_pricing_engine,
)
from app.features.tasks.schemas import TaskCreate, TaskUpdate, TaskPriceEstimateRequest


class TaskService:
    def __init__(
        self,
        task_repo: Repository[Task],
        location_repo: Repository[TaskLocation],
        attempt_repo: Repository[TaskDispatchAttempt],
        assignment_repo: Repository[TaskAssignment],
        history_repo: Repository[TaskStatusHistory],
        attachment_repo: Repository[TaskAttachment],
        user_repo: Repository[User],
        transaction_repo: Repository[Transaction],
        service_repo: Repository[Service],
        payment_gateway: PaymentGateway,
        notification_service: NotificationService,
        pricing_engine: PricingEngine,
    ):
        self.task_repo = task_repo
        self.location_repo = location_repo
        self.attempt_repo = attempt_repo
        self.assignment_repo = assignment_repo
        self.history_repo = history_repo
        self.attachment_repo = attachment_repo
        self.user_repo = user_repo
        self.transaction_repo = transaction_repo
        self.service_repo = service_repo
        self.payment_gateway = payment_gateway
        self.notification_service = notification_service
        self.pricing_engine = pricing_engine

    def _generate_pin(self) -> str:
        return f"{random.randint(0, 9999):04d}"

    async def create_task(self, customer_id: str, schema: TaskCreate) -> Task:
        # Fetch customer to get their region_id
        user = await self.user_repo.get(customer_id)
        region_id = user.region_id if user else None

        start_pin = self._generate_pin()
        completion_pin = self._generate_pin()

        # Calculate upfront pricing breakdown
        dist_km = calculate_locations_distance(schema.locations)

        pricing_req = PricingCalculationRequest(
            category_id=schema.category_id,
            service_id=schema.service_id,
            region_id=region_id,
            distance_km=dist_km,
        )
        breakdown = await self.pricing_engine.calculate_price(pricing_req)

        # Create Task
        task = Task(
            customer_id=customer_id,
            region_id=region_id,
            title=schema.title,
            description=schema.description,
            category_id=schema.category_id,
            service_id=schema.service_id,
            base_price=breakdown.base_price,
            distance_fee=breakdown.distance_fee,
            time_fee=breakdown.time_fee,
            urgency_fee=breakdown.urgency_fee,
            complexity_fee=breakdown.complexity_fee,
            surge_multiplier=breakdown.surge_multiplier,
            customer_total_price=breakdown.customer_total_price,
            platform_fee=breakdown.platform_fee,
            provider_payout=breakdown.provider_payout,
            scheduled_start_at=(
                schema.scheduled_start_at.replace(tzinfo=None)
                if schema.scheduled_start_at
                else None
            ),
            start_pin=start_pin,
            completion_pin=completion_pin,
            expires_at=(
                schema.expires_at.replace(tzinfo=None) if schema.expires_at else None
            ),
            status=TaskStatus.DRAFT,
        )
        task = await self.task_repo.add(task)

        # Create TaskLocations
        for loc in schema.locations:
            wkt_point = f"POINT({loc.longitude} {loc.latitude})"
            location = TaskLocation(
                task_id=task.id,
                location_type=loc.location_type,
                latitude=loc.latitude,
                longitude=loc.longitude,
                address=loc.address,
                city=loc.city,
                state=loc.state,
                country=loc.country,
                geography_point=wkt_point,
            )
            await self.location_repo.add(location)

        # Write TaskStatusHistory
        history = TaskStatusHistory(
            task_id=task.id,
            old_status=None,
            new_status=TaskStatus.DRAFT,
            changed_by=customer_id,
        )
        await self.history_repo.add(history)

        # Refresh to populate relationships
        await self.task_repo.refresh(task)

        return task

    async def confirm_draft(self, task_id: str, current_user_id: str) -> Task:
        task = await self.task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )
        if task.customer_id != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to confirm this task",
            )
        if task.status != TaskStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task is not in draft status",
            )

        updates = {"status": TaskStatus.OPEN, "updated_at": lagos_now()}
        await self.task_repo.update(task_id, updates)

        history = TaskStatusHistory(
            task_id=task.id,
            old_status=TaskStatus.DRAFT,
            new_status=TaskStatus.OPEN,
            changed_by=current_user_id,
        )
        await self.history_repo.add(history)
        await self.task_repo.refresh(task)
        return task

    async def cancel_draft(self, task_id: str, current_user_id: str) -> bool:
        task = await self.task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )
        if task.customer_id != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to cancel this task",
            )
        if task.status != TaskStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task is not in draft status",
            )

        await self.task_repo.delete(task_id)
        return True

    async def estimate_task_price(
        self,
        schema: TaskPriceEstimateRequest,
        customer_id: Optional[str] = None,
    ) -> PricingBreakdown:
        """Calculates upfront price breakdown for a task request before creation."""
        region_id = None
        if customer_id:
            user = await self.user_repo.get(customer_id)
            region_id = user.region_id if user else None

        dist_km = calculate_locations_distance(schema.locations)

        pricing_req = PricingCalculationRequest(
            category_id=schema.category_id,
            service_id=schema.service_id,
            region_id=region_id,
            distance_km=dist_km,
            is_urgent=schema.is_urgent,
        )

        return await self.pricing_engine.calculate_price(pricing_req)

    async def get_task(self, task_id: str) -> Optional[Task]:
        return await self.task_repo.get(task_id)

    async def get_tasks(
        self,
        page: int = 1,
        per_page: int = 20,
        status_filter: Optional[TaskStatus] = None,
        category_id: Optional[str] = None,
        service_id: Optional[str] = None,
        search: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        radius_km: Optional[float] = None,
        sort_by: str = "created_at",
        sort_desc: bool = True,
        region_id: Optional[str] = None,
        scheduled_start_at: Optional[datetime] = None,
        expires_at: Optional[datetime] = None,
        customer_id: Optional[str] = None,
    ) -> Tuple[List[Task], int]:
        statement = select(Task)
        count_statement = select(func.count()).select_from(Task)

        if status_filter:
            statement = statement.where(Task.status == status_filter)
            count_statement = count_statement.where(Task.status == status_filter)

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

        if region_id:
            statement = statement.where(Task.region_id == region_id)
            count_statement = count_statement.where(Task.region_id == region_id)

        if scheduled_start_at:
            statement = statement.where(
                col(Task.scheduled_start_at) >= scheduled_start_at
            )
            count_statement = count_statement.where(
                col(Task.scheduled_start_at) >= scheduled_start_at
            )

        if expires_at:
            statement = statement.where(col(Task.expires_at) <= expires_at)
            count_statement = count_statement.where(col(Task.expires_at) <= expires_at)

        if customer_id:
            statement = statement.where(Task.customer_id == customer_id)
            count_statement = count_statement.where(Task.customer_id == customer_id)

        if latitude is not None and longitude is not None and radius_km is not None:
            # pyrefly: ignore [bad-argument-type]
            statement = statement.join(TaskLocation, Task.id == TaskLocation.task_id)
            count_statement = count_statement.join(
                TaskLocation,
                Task.id == TaskLocation.task_id,  # pyrefly: ignore [bad-argument-type]
            )

            dialect_name = (
                self.task_repo.session.bind.dialect.name
                if self.task_repo.session.bind
                else "postgresql"
            )
            if dialect_name == "sqlite":
                delta_lat = radius_km / 111.0
                cos_lat = math.cos(math.radians(latitude))
                cos_lat = max(cos_lat, 0.1)
                delta_lng = radius_km / (111.0 * cos_lat)

                spatial_filter = (
                    TaskLocation.latitude >= latitude - delta_lat,
                    TaskLocation.latitude <= latitude + delta_lat,
                    TaskLocation.longitude >= longitude - delta_lng,
                    TaskLocation.longitude <= longitude + delta_lng,
                )
                statement = statement.where(*spatial_filter)
                count_statement = count_statement.where(*spatial_filter)

                # Approximate Euclidean distance in km for sqlite
                distance_expr = func.sqrt(
                    func.pow((TaskLocation.latitude - latitude) * 111.0, 2)
                    + func.pow(
                        (TaskLocation.longitude - longitude) * 111.0 * cos_lat, 2
                    )
                )
                statement = statement.options(
                    # pyrefly: ignore [bad-argument-type]
                    contains_eager(Task.locations).with_expression(
                        # pyrefly: ignore [bad-argument-type]
                        TaskLocation.distance_km,
                        distance_expr,
                    )
                )
            else:
                target_point = func.ST_SetSRID(
                    func.ST_MakePoint(longitude, latitude), 4326
                )
                statement = statement.where(
                    func.ST_DWithin(
                        cast(TaskLocation.geography_point, Geography),
                        cast(target_point, Geography),
                        radius_km * 1000.0,
                    )
                )
                count_statement = count_statement.where(
                    func.ST_DWithin(
                        cast(TaskLocation.geography_point, Geography),
                        cast(target_point, Geography),
                        radius_km * 1000.0,
                    )
                )

                distance_expr = (
                    func.ST_Distance(
                        cast(TaskLocation.geography_point, Geography),
                        cast(target_point, Geography),
                    )
                    / 1000.0
                )

                statement = statement.options(
                    contains_eager(Task.locations).with_expression(TaskLocation.distance_km, distance_expr)  # type: ignore
                )

        count_result = await self.task_repo.execute(count_statement)
        total = count_result.first() or 0

        if sort_by and hasattr(Task, sort_by):
            sort_col = getattr(Task, sort_by)
            statement = statement.order_by(desc(sort_col) if sort_desc else sort_col)

        statement = statement.offset((page - 1) * per_page).limit(per_page)

        results = await self.task_repo.execute(statement)
        tasks = list(results.unique().all())

        return tasks, total

    async def update_task(
        self,
        task_id: str,
        current_user_id: str,
        schema: TaskUpdate,
        is_admin: bool = False,
    ) -> Task:
        task = await self.task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )
        if not is_admin and task.customer_id != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to update this task",
            )

        updates = {}
        for key in [
            "title",
            "description",
            "category_id",
            "service_id",
            "scheduled_start_at",
            "expires_at",
        ]:
            val = getattr(schema, key, None)
            if val is not None:
                updates[key] = val

        old_status = task.status
        new_status = schema.status
        if new_status is not None and new_status != old_status:
            updates["status"] = new_status

        if updates:
            updates["updated_at"] = lagos_now()
            await self.task_repo.update(task_id, updates)
            await self.task_repo.refresh(task)

        if new_status is not None and new_status != old_status:
            history = TaskStatusHistory(
                task_id=task.id,
                old_status=old_status,
                new_status=new_status,
                changed_by=current_user_id,
            )
            await self.history_repo.add(history)

        await self.task_repo.refresh(task)
        return task

    async def delete_task(
        self, task_id: str, current_user_id: str, is_admin: bool = False
    ) -> bool:
        task = await self.task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )
        if not is_admin and task.customer_id != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to cancel this task",
            )

        if task.status in [TaskStatus.COMPLETED, TaskStatus.CANCELLED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot cancel a task that is already completed or cancelled",
            )

        old_status = task.status
        await self.task_repo.update(
            task_id, {"status": TaskStatus.CANCELLED, "updated_at": lagos_now()}
        )

        history = TaskStatusHistory(
            task_id=task.id,
            old_status=old_status,
            new_status=TaskStatus.CANCELLED,
            changed_by=current_user_id,
        )
        await self.history_repo.add(history)
        return True

    async def initiate_task_payment(
        self, task_id: str, customer_id: str
    ) -> PaymentInitializationResponse:
        task = await self.task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )
        if task.customer_id != customer_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to initiate payment for this task",
            )
        if task.status not in [TaskStatus.OPEN, TaskStatus.SEARCHING]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment can only be initialized for open tasks",
            )

        customer = await self.user_repo.get(customer_id)
        if not customer or not customer.email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Customer email is required for payment",
            )
        fullname = None
        if (
            customer.provider_profile
            and customer.provider_profile.first_name
            and customer.provider_profile.last_name
        ):
            fullname = f"{customer.provider_profile.first_name} {customer.provider_profile.last_name}"

        total_amount = task.customer_total_price or 0.0
        if total_amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task total price has not been calculated or is invalid",
            )

        payment_response = await self.payment_gateway.receive_payment(
            email=customer.email,
            amount=total_amount,
            user_id=customer.id,
            fullname=fullname,
            phone_number=customer.phone_number,
            metadata={"task_id": task.id},
        )

        if not payment_response.checkout_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Payment gateway could not generate checkout url",
            )

        return payment_response

    async def get_providers_near_task(
        self, task_id: str, radius_km: float
    ) -> List[User]:
        task = await self.task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )

        # Get task location
        stmt_loc = (
            select(TaskLocation).where(col(TaskLocation.task_id) == task_id).limit(1)
        )
        res_loc = await self.task_repo.execute(stmt_loc)
        task_loc = res_loc.one_or_none()

        if not task_loc or not task_loc.geography_point:
            return []

        # Query providers
        stmt = TaskQueries.get_providers_near_task(
            task, task_loc, radius_km, select_ids_only=False
        )

        res = await self.user_repo.execute(stmt)
        return list(res.all())


def get_task_service(
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    location_repo: Repository[TaskLocation] = Depends(GetRepository(TaskLocation)),
    attempt_repo: Repository[TaskDispatchAttempt] = Depends(
        GetRepository(TaskDispatchAttempt)
    ),
    assignment_repo: Repository[TaskAssignment] = Depends(
        GetRepository(TaskAssignment)
    ),
    history_repo: Repository[TaskStatusHistory] = Depends(
        GetRepository(TaskStatusHistory)
    ),
    attachment_repo: Repository[TaskAttachment] = Depends(
        GetRepository(TaskAttachment)
    ),
    user_repo: Repository[User] = Depends(GetRepository(User)),
    transaction_repo: Repository[Transaction] = Depends(GetRepository(Transaction)),
    service_repo: Repository[Service] = Depends(GetRepository(Service)),
    payment_gateway: PaymentGateway = Depends(get_paystack_gateway),
    notification_service: NotificationService = Depends(get_notification_service),
    pricing_engine: PricingEngine = Depends(get_pricing_engine),
) -> TaskService:
    return TaskService(
        task_repo=task_repo,
        location_repo=location_repo,
        attempt_repo=attempt_repo,
        assignment_repo=assignment_repo,
        history_repo=history_repo,
        attachment_repo=attachment_repo,
        user_repo=user_repo,
        transaction_repo=transaction_repo,
        service_repo=service_repo,
        payment_gateway=payment_gateway,
        notification_service=notification_service,
        pricing_engine=pricing_engine,
    )
