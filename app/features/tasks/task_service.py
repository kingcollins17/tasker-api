import math
import random
from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import Depends, HTTPException, status
from geoalchemy2 import Geography
from sqlalchemy import cast, update
from sqlalchemy.orm import contains_eager
from sqlmodel import col, desc, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.models.notifications import (
    NotificationChannel,
    NotificationPriority,
    NotificationType,
)
from app.core.models.payments import PayoutQueue
from app.core.models.services import PricingRule, Service, ServiceCategory
from app.core.models.tasks import (
    CancelledBy,
    DispatchAttemptStatus,
    DispatchSession,
    LocationType,
    PriceAdjustmentStatus,
    Task,
    TaskAssignment,
    TaskAssignmentStatus,
    TaskAttachment,
    TaskDispatchAttempt,
    TaskEventHistory,
    TaskLocation,
    TaskPriceAdjustment,
    TaskStatus,
)
from app.core.models.transactions import Transaction, TransactionStatus, TransactionType
from app.core.models.users import DutyStatus, ProviderProfile, User, UserLocation, UserType
from app.core.queries.task_queries import TaskQueries
from app.core.repository import GetRepository, QueryOptions, Repository
from app.core.services.logger_service import LoggerService, get_logger_service
from app.core.services.payment import (
    PaymentGateway,
    PaymentInitializationResponse,
    get_paystack_gateway,
)
from app.core.utils.currency import to_naira
from app.core.utils.datetime_helper import lagos_now
from app.core.utils.geo import calculate_locations_distance
from app.features.credibility.credibility_service import (
    CredibilityService,
    get_credibility_service,
)
from app.features.notifications.schemas import CreateNotification
from app.features.notifications.notification_service import (
    NotificationService,
    get_notification_service,
)
from app.features.services.pricing_engine import (
    PricingBreakdown,
    PricingCalculationRequest,
    PricingEngine,
    get_pricing_engine,
)
from app.features.tasks.dispatch_service import DispatchService
from app.features.tasks.schemas import (
    PriceAdjustmentCreate,
    PriceAdjustmentRespond,
    TaskCreate,
    TaskPriceEstimateRequest,
    TaskUpdate,
)


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
        price_adjustment_repo: Repository[TaskPriceAdjustment],
        payout_repo: Optional[Repository[PayoutQueue]] = None,
        session: Optional[AsyncSession] = None,
        system_logger: Optional[LoggerService] = None,
        credibility_service: Optional[CredibilityService] = None,
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
        self.price_adjustment_repo = price_adjustment_repo
        self.payout_repo = payout_repo
        self.session = session or task_repo.session
        self.system_logger = system_logger
        self.credibility_service = credibility_service

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
        
        # Check task status: COMPLETED or CANCELLED tasks cannot be cancelled
        if task.status in [TaskStatus.COMPLETED, TaskStatus.CANCELLED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot cancel task with status {task.status.value}.",
            )
        
        # Get assignment details if available
        assignment = task.assignment
        provider_id = assignment.provider_id if assignment else None
        was_in_progress = task.status == TaskStatus.IN_PROGRESS
        pin_provided = cancellation_pin is not None and cancellation_pin.strip() != ""
        pin_valid = False
        
        # For IN_PROGRESS tasks with an assignment, validate cancellation_pin
        if was_in_progress and assignment:
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
        
        # Update assignment status if assignment exists
        if assignment:
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
        }
        if provider_id:
            event_data["provider_id"] = provider_id
        
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
        
        # Send notification to provider if assigned
        if provider_id:
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
                    type=NotificationType.SYSTEM_ALERT,
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
        elif getattr(customer, "first_name", None) and getattr(customer, "last_name", None):
            fullname = f"{customer.first_name} {customer.last_name}"

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
            metadata={
                "task_id": task.id,
                "user_id": customer.id,
                "type": "task_payment",
            },
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

    async def request_price_adjustment(
        self,
        task_id: str,
        provider_id: str,
        schema: PriceAdjustmentCreate,
    ) -> TaskPriceAdjustment:
        task = await self.task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found",
            )

        if task.assigned_provider_id != provider_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the assigned provider can request a price adjustment for this task",
            )

        if task.status not in (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Price adjustments can only be requested for active tasks",
            )

        stmt = select(TaskPriceAdjustment).where(
            TaskPriceAdjustment.task_id == task_id,
            TaskPriceAdjustment.status == PriceAdjustmentStatus.PENDING,
        )
        res = await self.price_adjustment_repo.execute(stmt)
        existing = res.first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A price adjustment request is already pending for this task",
            )

        adjustment = TaskPriceAdjustment(
            task_id=task_id,
            amount=schema.amount,
            description=schema.description,
            requested_by=provider_id,
            status=PriceAdjustmentStatus.PENDING,
        )
        adjustment = await self.price_adjustment_repo.add(adjustment)

        await self.log_task_event(
            task_id,
            event="PRICE_ADJUSTMENT_REQUESTED",
            reason=schema.description,
            adjustment_id=adjustment.id,
            amount=schema.amount,
            requested_by=provider_id,
        )

        if task.customer_id:
            await self.notification_service.notify(
                recepients=[task.customer_id],
                title="Price Adjustment Requested",
                body=f"Provider requested a price adjustment of {to_naira(schema.amount)} for task: {task.title}",
                type=NotificationType.SYSTEM_ALERT,
                data={
                    "task_id": task_id,
                    "adjustment_id": adjustment.id,
                    "amount": schema.amount,
                    "description": schema.description,
                },
            )

        return adjustment

    async def respond_to_price_adjustment(
        self,
        task_id: str,
        adjustment_id: str,
        customer_id: str,
        schema: PriceAdjustmentRespond,
    ) -> TaskPriceAdjustment:
        task = await self.task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found",
            )

        if task.customer_id != customer_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the task customer can respond to price adjustments",
            )

        adjustment = await self.price_adjustment_repo.get(adjustment_id)
        if not adjustment or adjustment.task_id != task_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Price adjustment request not found",
            )

        if adjustment.status != PriceAdjustmentStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This price adjustment request has already been processed",
            )

        if schema.approved:
            adjustment.status = PriceAdjustmentStatus.APPROVED
            new_customer_price = max(0.0, float(adjustment.amount or 0.0))
            service = await self.service_repo.get(task.service_id) if task.service_id else None
            take_rate = service.take_rate if (service and service.take_rate is not None) else 0.15
            new_platform_fee = round(new_customer_price * take_rate, 2)
            new_payout = round(new_customer_price - new_platform_fee, 2)
            await self.task_repo.update(task.id, {
                "customer_total_price": new_customer_price,
                "platform_fee": new_platform_fee,
                "provider_payout": new_payout,
            })

            event_name = "PRICE_ADJUSTMENT_APPROVED"
            notif_title = "Price Adjustment Approved"
            notif_body = f"Customer approved your price adjustment request of {to_naira(adjustment.amount)} for task: {task.title}"
            notif_type = NotificationType.SYSTEM_ALERT
        else:
            adjustment.status = PriceAdjustmentStatus.REJECTED
            event_name = "PRICE_ADJUSTMENT_REJECTED"
            notif_title = "Price Adjustment Declined"
            notif_body = f"Customer declined your price adjustment request of {to_naira(adjustment.amount)} for task: {task.title}"
            notif_type = NotificationType.SYSTEM_ALERT

        adjustment = await self.price_adjustment_repo.add(adjustment)

        await self.log_task_event(
            task_id,
            event=event_name,
            reason=adjustment.description,
            adjustment_id=adjustment.id,
            amount=adjustment.amount,
            customer_id=customer_id,
        )

        if task.assigned_provider_id:
            await self.notification_service.notify(
                recepients=[task.assigned_provider_id],
                title=notif_title,
                body=notif_body,
                type=notif_type,
                data={
                    "task_id": task_id,
                    "adjustment_id": adjustment.id,
                    "status": adjustment.status,
                    "amount": adjustment.amount,
                },
            )

        return adjustment


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
    price_adjustment_repo: Repository[TaskPriceAdjustment] = Depends(
        GetRepository(TaskPriceAdjustment)
    ),
    payout_repo: Repository[PayoutQueue] = Depends(GetRepository(PayoutQueue)),
    session: AsyncSession = Depends(get_session),
    system_logger: LoggerService = Depends(get_logger_service),
    credibility_service: CredibilityService = Depends(get_credibility_service),
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
        price_adjustment_repo=price_adjustment_repo,
        payout_repo=payout_repo,
        session=session,
        system_logger=system_logger,
        credibility_service=credibility_service,
    )
