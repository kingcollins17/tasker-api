from app.core.models.users import DutyStatus
import math
import random
from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import Depends, HTTPException, status
from geoalchemy2 import Geography
from sqlalchemy import cast, update
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
    DispatchSession,
    LocationType,
    Task,
    TaskAssignment,
    TaskAssignmentStatus,
    TaskAttachment,
    TaskDispatchAttempt,
    TaskEventHistory,
    TaskLocation,
    TaskStatus,
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
        history_repo: Repository[TaskEventHistory],
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

    async def log_task_event(
        self,
        task_id: str,
        event: str,
        reason: Optional[str] = None,
        **kwargs,
    ) -> TaskEventHistory:
        payload = dict(kwargs)
        history = TaskEventHistory(
            task_id=task_id,
            event=event,
            reason=reason,
            data=payload if payload else None,
        )
        await self.history_repo.add(history)
        return history

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

        await self.log_task_event(
            task_id=task.id,
            event="task_created",
            reason="Customer created task",
            status=TaskStatus.DRAFT.value,
            customer_id=customer_id,
        )

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

        await self.log_task_event(
            task_id=task.id,
            event="task_confirmed",
            reason="Customer confirmed draft task",
            from_status=TaskStatus.DRAFT.value,
            to_status=TaskStatus.OPEN.value,
            user_id=current_user_id,
        )
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

    async def cancel_task(
        self,
        task_id: str,
        current_user_id: str,
        cancellation_reason: Optional[str] = None,
        cancellation_pin: Optional[str] = None,
    ) -> Task:
        """Cancel an assigned or in-progress task by customer.
        
        If task is ASSIGNED:
        - Marks task and assignment as cancelled
        - Notifies provider of cancellation
        - Frees up provider for other tasks
        
        If task is IN_PROGRESS:
        - Validates cancellation_pin if provided (indicates agreement with provider)
        - If no pin: customer acting alone, incurs penalty
        - Marks task as cancelled by customer
        - Notifies provider
        """
        from app.core.models.tasks import CancelledBy
        
        task = await self.task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )
        
        # Authorization: only customer who created task can cancel
        if task.customer_id != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to cancel this task",
            )
        
        # Only ASSIGNED or IN_PROGRESS tasks can be cancelled
        if task.status not in [TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot cancel task with status {task.status.value}. Only ASSIGNED or IN_PROGRESS tasks can be cancelled.",
            )
        
        # Get assignment details
        assignment = task.assignment
        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No assignment found for this task",
            )
        
        provider_id = assignment.provider_id
        was_in_progress = task.status == TaskStatus.IN_PROGRESS
        pin_provided = cancellation_pin is not None and cancellation_pin.strip() != ""
        pin_valid = False
        
        # For IN_PROGRESS tasks, validate cancellation_pin
        if was_in_progress:
            if pin_provided:
                # Check if pin matches the assignment's cancellation_pin
                pin_valid = assignment.cancellation_pin == cancellation_pin
                if not pin_valid:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid cancellation PIN",
                    )
        
        # Update task
        old_status = task.status
        task_updates = {
            "status": TaskStatus.CANCELLED,
            "cancellation_reason": cancellation_reason,
            "cancelled_by": CancelledBy.CUSTOMER,
            "updated_at": lagos_now(),
        }
        await self.task_repo.update(task_id, task_updates)
        
        # Update assignment status
        assignment_updates = {
            "status": TaskAssignmentStatus.CANCELLED,
            "updated_at": lagos_now(),
        }
        await self.assignment_repo.update(assignment.id, assignment_updates)
        
        # Log event
        event_data = {
            "from_status": old_status.value,
            "to_status": TaskStatus.CANCELLED.value,
            "cancelled_by": CancelledBy.CUSTOMER.value,
            "user_id": current_user_id,
            "provider_id": provider_id,
        }
        
        if was_in_progress:
            event_data["pin_provided"] = str(pin_provided)
            event_data["pin_valid"] = str(pin_valid)
            if not pin_provided:
                event_data["penalty_applied"] = "true"
        
        await self.log_task_event(
            task_id=task.id,
            event="task_cancelled_by_customer",
            reason=cancellation_reason or "Customer cancelled the task",
            **event_data,
        )
        
        # Send notification to provider
        provider = await self.user_repo.get(provider_id)
        if provider:
            notification_title = "Task Cancelled"
            if was_in_progress:
                if pin_valid:
                    notification_body = f"The customer has cancelled the task '{task.title}' by mutual agreement."
                else:
                    notification_body = f"The customer has cancelled the task '{task.title}' they started. This may result in a penalty charge."
            else:
                notification_body = f"The customer has cancelled the assigned task '{task.title}'."
            
            await self.notification_service.notify(
                recepients=[provider_id],
                title=notification_title,
                body=notification_body,
                type=NotificationType.TASK_CANCELLED,
                data={
                    "task_id": task.id,
                    "task_title": task.title,
                    "cancelled_by": CancelledBy.CUSTOMER.value,
                    "agreement_pin_used": pin_valid,
                },
                channels=["push"],
                expires_at=None,
            )
        
        # Refresh task to return updated state
        await self.task_repo.refresh(task)
        return task

    async def redispatch_task(
        self,
        task_id: str,
        current_user_id: str,
        feedback: Optional[str] = None,
    ) -> Task:
        """Redispatch an ASSIGNED task to find a different provider.
        
        Flow:
        1. Validates task is ASSIGNED (customer has not started work yet)
        2. Gets currently assigned provider
        3. Cancels current assignment
        4. Clears task.assigned_provider_id
        5. Creates a fake CANCELLED dispatch attempt for old provider (to exclude them)
        6. Closes old dispatch session
        7. Moves task back to OPEN status
        8. Triggers new dispatch session to find replacement provider
        9. Notifies old provider of redispatch
        
        This ensures the matching engine will not ping the old provider again,
        but will consider all other providers (including those who declined before).
        """
        from app.core.models.tasks import CancelledBy, DispatchSessionStatus
        
        task = await self.task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )
        
        # Authorization: only customer who created task can redispatch
        if task.customer_id != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to redispatch this task",
            )
        
        # Only ASSIGNED tasks can be redispatched (not yet started)
        if task.status != TaskStatus.ASSIGNED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot redispatch task with status {task.status.value}. Only ASSIGNED tasks can be redispatched.",
            )
        
        # Check maximum allowed redispatches limit
        max_allowed = (
            task.max_customer_redispatches
            if task.max_customer_redispatches is not None
            else 3
        )
        stmt_redispatch_count = select(func.count(col(DispatchSession.id))).where(
            col(DispatchSession.task_id) == task_id,
            col(DispatchSession.is_redispatch) == True,  # noqa: E712
        )
        res_count = await self.task_repo.execute(stmt_redispatch_count)
        redispatch_count = res_count.first() or 0

        if redispatch_count >= max_allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum allowed redispatches ({max_allowed}) reached for this task.",
            )
        
        # Get assignment details
        assignment = task.assignment
        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No assignment found for this task",
            )
        
        old_provider_id = assignment.provider_id
        old_assignment_id = assignment.id
        
        # Step 1: Cancel the current assignment
        assignment_updates = {
            "status": TaskAssignmentStatus.CANCELLED,
            "updated_at": lagos_now(),
        }
        await self.assignment_repo.update(old_assignment_id, assignment_updates)

        # Mark old provider duty status back to ONLINE_AVAILABLE if currently ON_TASK or ON_DISPATCH
        stmt_reset_provider = (
            update(ProviderProfile)
            .where(
                col(ProviderProfile.user_id) == old_provider_id,
                col(ProviderProfile.duty_status).in_([DutyStatus.ON_TASK, DutyStatus.ON_DISPATCH]),
            )
            .values(duty_status=DutyStatus.ONLINE_AVAILABLE)
        )
        await self.task_repo.execute(stmt_reset_provider)
        
        # Step 2: Cancel ALL active/open dispatch sessions for this task
        stmt_cancel_sessions = (
            update(DispatchSession)
            .where(
                col(DispatchSession.task_id) == task_id,
                col(DispatchSession.status).in_([
                    DispatchSessionStatus.SEARCHING,
                    DispatchSessionStatus.ASSIGNED,
                ]),
            )
            .values(
                status=DispatchSessionStatus.CANCELLED,
                updated_at=lagos_now(),
            )
        )
        await self.task_repo.execute(stmt_cancel_sessions)

        # Step 3: Cancel ALL pending and accepted dispatch attempts for this task
        stmt_cancel_attempts = (
            update(TaskDispatchAttempt)
            .where(
                col(TaskDispatchAttempt.task_id) == task_id,
                col(TaskDispatchAttempt.status).in_([
                    DispatchAttemptStatus.PENDING,
                    DispatchAttemptStatus.ACCEPTED,
                ]),
            )
            .values(
                status=DispatchAttemptStatus.CANCELED,
                responded_at=lagos_now(),
            )
        )
        await self.attempt_repo.execute(stmt_cancel_attempts)
        
        # Step 4: Move task back to OPEN and clear assigned provider
        old_status = task.status
        task_updates = {
            "status": TaskStatus.OPEN,
            "assigned_provider_id": None,
            "updated_at": lagos_now(),
        }
        await self.task_repo.update(task_id, task_updates)
        
        # Step 5: Log the redispatch event
        await self.log_task_event(
            task_id=task.id,
            event="task_redispatched",
            reason=feedback or "Customer requested redispatch to different provider",
            from_status=old_status.value,
            to_status=TaskStatus.OPEN.value,
            customer_id=current_user_id,
            old_provider_id=old_provider_id,
            feedback=feedback,
        )
        
        # Step 6: Notify old provider of redispatch
        old_provider = await self.user_repo.get(old_provider_id)
        if old_provider:
            notification_body = f"The customer has requested a different provider for the task '{task.title}'."
            if feedback:
                notification_body += f" Reason: {feedback}"
            
            await self.notification_service.notify(
                recepients=[old_provider_id],
                title="Task Redispatched",
                body=notification_body,
                type=NotificationType.SYSTEM_ALERT,
                data={
                    "task_id": task.id,
                    "task_title": task.title,
                    "feedback": feedback,
                },
                channels=["push"],
                expires_at=None,
            )
        
        # Step 7: Trigger new dispatch session
        from app.features.tasks.celery.dispatch import start_dispatch_session_task
        # pyrefly: ignore [not-callable]
        start_dispatch_session_task.delay(
            task.id,
            is_redispatch=True,
            redispatch_reason=feedback,
            exclude_previous_sessions=False,
            excluded_provider_ids=[old_provider_id],
        )  # type: ignore
        
        # Refresh and return updated task
        await self.task_repo.refresh(task)
        return task

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
        status_filter: Optional[List[TaskStatus]] = None,
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
            statement = statement.where(col(Task.status).in_(status_filter))
            count_statement = count_statement.where(
                col(Task.status).in_(status_filter)
            )

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
            statement = statement.join(TaskLocation, col(Task.id) == col(TaskLocation.task_id))
            count_statement = count_statement.join(
                TaskLocation,
                col(Task.id) == col(TaskLocation.task_id),  # pyrefly: ignore [bad-argument-type]
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
                    contains_eager(Task.locations).with_expression( # type: ignore
                        # pyrefly: ignore [bad-argument-type]
                        TaskLocation.distance_km, # type: ignore
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
            "scheduled_start_at",
            "expires_at",
        ]:
            val = getattr(schema, key, None)
            if val is not None:
                updates[key] = val

        if updates:
            updates["updated_at"] = lagos_now()
            await self.task_repo.update(task_id, updates)
            await self.task_repo.refresh(task)

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

        await self.log_task_event(
            task_id=task.id,
            event="task_cancelled",
            reason="Task cancelled by user",
            from_status=old_status.value if isinstance(old_status, TaskStatus) else old_status,
            to_status=TaskStatus.CANCELLED.value,
            user_id=current_user_id,
        )
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
    history_repo: Repository[TaskEventHistory] = Depends(
        GetRepository(TaskEventHistory)
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
