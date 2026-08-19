from app.core.utils.timer import Timer
from app.core.services.logger_service import LoggerService, get_logger_service
from app.core.models.users import KYCStatus
from fastapi import APIRouter, Depends, status, HTTPException, Query
from typing import Optional
from pydantic import BaseModel
from sqlmodel import select, func, col
from sqlalchemy import desc, or_

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.deps import GetCurrentUser
from app.features.users.schemas import UserResponse
from app.core.models.users import UserType, User
from app.core.schemas.users import MinimalProviderResponse
from app.core.models.tasks import (
    DispatchAttemptStatus,
    PaymentMode,
    Task,
    TaskAssignment,
    TaskAssignmentStatus,
    TaskStatus,
    TaskDispatchAttempt,
)
from app.core.repository import GetRepository, Repository
from app.core.utils.datetime_helper import lagos_now
from app.features.tasks.schemas import (
    TaskAssignmentResponse,
    TaskAssignmentWithTaskResponse,
    TaskMinimalResponse,
    TaskDispatchAttemptResponse,
)
from app.features.tasks.celery.completion import complete_task_assignment
from app.features.tasks.dispatch_service import (
    DispatchEventService,
    get_dispatch_event_service,
)
from app.core.error_handler import AppErrorHandler


class DispatchRespondBody(BaseModel):
    """Request body for a provider responding to a dispatch ping."""

    status: DispatchAttemptStatus


class PinBody(BaseModel):
    """PIN verification body used for task start."""

    pin: str


class TaskCompleteBody(BaseModel):
    """PIN verification body for task completion with payment mode selection."""

    pin: str
    payment_mode: PaymentMode = PaymentMode.CASH


router = APIRouter(tags=["Assignments"])


@router.get(
    "/assignments",
    response_model=BaseAPIResponse[PaginatedData[TaskAssignmentWithTaskResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_my_assignments(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status_filter: Optional[TaskAssignmentStatus] = Query(None, alias="status"),
    task_id: Optional[str] = Query(None),
    sort_by: str = Query("assigned_at"),
    sort_desc: bool = Query(True),
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)
    ),
    assignment_repo: Repository[TaskAssignment] = Depends(
        GetRepository(TaskAssignment)
    ),
    system_logger: LoggerService = Depends(get_logger_service),
):
    """Retrieve a paginated list of assignments for the current user."""
    try:
        timer = Timer()
        timer.start()
        query = select(TaskAssignment, Task).join(
            # pyrefly: ignore [bad-argument-type]
            Task,
            # pyrefly: ignore [bad-argument-type]
            col(TaskAssignment.task_id) == Task.id,
        )

        query = query.where(TaskAssignment.provider_id == current_user.id)

        if status_filter:
            query = query.where(TaskAssignment.status == status_filter)
        if task_id:
            query = query.where(TaskAssignment.task_id == task_id)

        # Counting records
        # pyrefly: ignore [bad-argument-type]
        count_query = select(func.count(col(TaskAssignment.id))).join(
            # pyrefly: ignore [bad-argument-type]
            Task,
            # pyrefly: ignore [bad-argument-type]
            col(TaskAssignment.task_id) == Task.id,
        )
        if current_user.type == UserType.PROVIDER:
            count_query = count_query.where(
                col(TaskAssignment.provider_id) == current_user.id
            )
        elif current_user.type == UserType.CUSTOMER:
            count_query = count_query.where(col(Task.customer_id) == current_user.id)
        else:
            count_query = count_query.where(col(TaskAssignment.id) == "0")

        if status_filter:
            count_query = count_query.where(col(TaskAssignment.status) == status_filter)
        if task_id:
            count_query = count_query.where(col(TaskAssignment.task_id) == task_id)

        total = (await assignment_repo.execute(count_query)).one()

        if hasattr(TaskAssignment, sort_by):
            order_column = getattr(TaskAssignment, sort_by)
            if sort_desc:
                query = query.order_by(desc(order_column))
            else:
                query = query.order_by(order_column)

        query = query.offset((page - 1) * per_page).limit(per_page)

        assignments_result = await assignment_repo.execute(query)
        assignments = assignments_result.unique().all()

        items = [
            TaskAssignmentWithTaskResponse.model_validate(assignment_model).model_copy(
                update={
                    "task": (
                        TaskMinimalResponse.model_validate(task_model)
                        if task_model
                        else None
                    )
                }
            )
            for assignment_model, task_model in assignments
        ]

        data = PaginatedData[TaskAssignmentWithTaskResponse](
            items=items,
            total=total,
            page=page,
            per_page=per_page,
        )
        await system_logger.metric(
            "get_my_assignments", timer.stop(), source="assignments.get_my_assignments"
        )
        return BaseAPIResponse[PaginatedData[TaskAssignmentWithTaskResponse]](
            data=data,
            detail="Assignments retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn(
            "get_my_assignments failed",
            source="assignments.get_my_assignments",
            metadata={"detail": str(e.detail) if hasattr(e, "detail") else str(e)},
        )
        raise
    except Exception as e:
        await system_logger.error(
            f"get_my_assignments error: {str(e)}",
            source="assignments.get_my_assignments",
        )
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving assignments.",
        )


@router.get(
    "/assignments/current",
    response_model=BaseAPIResponse[TaskAssignmentWithTaskResponse],
    status_code=status.HTTP_200_OK,
)
async def get_current_assignment(
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_type=UserType.PROVIDER,
            required_phone_verified=True,
            required_email_verified=True,
        )
    ),
    assignment_repo: Repository[TaskAssignment] = Depends(
        GetRepository(TaskAssignment)
    ),
    user_repo: Repository[User] = Depends(GetRepository(User)),
    system_logger: LoggerService = Depends(get_logger_service),
):
    """Retrieve the provider's most recent active assignment."""
    try:
        timer = Timer()
        timer.start()

        stmt = (
            select(TaskAssignment, Task)
            .join(
                Task,
                col(TaskAssignment.task_id) == Task.id,
            )
            .where(
                col(TaskAssignment.provider_id) == current_user.id,
                col(TaskAssignment.status).in_(
                    [
                        TaskAssignmentStatus.ASSIGNED,
                        TaskAssignmentStatus.IN_PROGRESS,
                    ]
                ),
            )
            .order_by(desc(col(TaskAssignment.assigned_at)))
            .limit(1)
        )

        row = (await assignment_repo.execute(stmt)).first()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active assignment found for this provider.",
            )

        assignment_model, task_model = row
        assignment_data = TaskAssignmentWithTaskResponse.model_validate(
            assignment_model
        )
        assignment_data.task = TaskMinimalResponse.model_validate(task_model)

        provider_user = await user_repo.get(assignment_model.provider_id)
        if provider_user:
            assignment_data.provider = MinimalProviderResponse.from_user(provider_user)

        await system_logger.metric(
            "get_current_assignment",
            timer.stop(),
            source="assignments.get_current_assignment",
        )
        return BaseAPIResponse[TaskAssignmentWithTaskResponse](
            data=assignment_data,
            detail="Current assignment retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn(
            "get_current_assignment failed",
            source="assignments.get_current_assignment",
            metadata={"detail": e.detail if hasattr(e, "detail") else str(e)},
        )
        raise
    except Exception as e:
        await system_logger.error(
            f"get_current_assignment error: {str(e)}",
            source="assignments.get_current_assignment",
        )
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving the current assignment.",
        )


@router.get(
    "/tasks/{task_id}/assignment",
    response_model=BaseAPIResponse[TaskAssignmentWithTaskResponse],
    status_code=status.HTTP_200_OK,
)
async def get_task_assignment(
    task_id: str,
    current_user: UserResponse = Depends(GetCurrentUser()),
    assignment_repo: Repository[TaskAssignment] = Depends(
        GetRepository(TaskAssignment)
    ),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    user_repo: Repository[User] = Depends(GetRepository(User)),
    system_logger: LoggerService = Depends(get_logger_service),
):
    """Retrieve the assignment for a specific task if available."""
    try:
        timer = Timer()
        timer.start()
        task = await task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )

        assignment_result = await assignment_repo.execute(
            select(TaskAssignment).where(TaskAssignment.task_id == task_id)
        )
        assignment = assignment_result.first()

        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assignment not found for this task",
            )

        # Authorization check: only customer of task or provider assigned can view
        if (
            task.customer_id != current_user.id
            and assignment.provider_id != current_user.id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this assignment",
            )

        assignment_data = TaskAssignmentWithTaskResponse.model_validate(assignment)
        assignment_data.task = TaskMinimalResponse.model_validate(task)
        if assignment.provider_id:
            provider_user = await user_repo.get(assignment.provider_id)
            if provider_user:
                assignment_data.provider = MinimalProviderResponse.from_user(
                    provider_user
                )

        await system_logger.metric(
            "get_task_assignment",
            timer.stop(),
            source="assignments.get_task_assignment",
        )
        return BaseAPIResponse[TaskAssignmentWithTaskResponse](
            data=assignment_data,
            detail="Task assignment retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn(
            "get_task_assignment failed",
            source="assignments.get_task_assignment",
            metadata={"detail": str(e.detail) if hasattr(e, "detail") else str(e)},
        )
        raise
    except Exception as e:
        await system_logger.error(
            f"get_task_assignment error: {str(e)}",
            source="assignments.get_task_assignment",
        )
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving task assignment.",
        )


@router.get(
    "/dispatches/current",
    response_model=BaseAPIResponse[TaskDispatchAttemptResponse],
    status_code=status.HTTP_200_OK,
)
async def get_provider_current_dispatch(
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_type=UserType.PROVIDER,
            required_phone_verified=True,
            required_email_verified=True,
        )
    ),
    attempt_repo: Repository[TaskDispatchAttempt] = Depends(
        GetRepository(TaskDispatchAttempt)
    ),
    system_logger: LoggerService = Depends(get_logger_service),
):
    """Retrieve the provider's latest unexpired pending dispatch attempt."""
    try:
        timer = Timer()
        timer.start()

        now = lagos_now()
        stmt = (
            select(TaskDispatchAttempt, User)
            .join(
                User, col(TaskDispatchAttempt.provider_id) == col(User.id), isouter=True
            )
            .where(
                col(TaskDispatchAttempt.provider_id) == current_user.id,
                col(TaskDispatchAttempt.status) == DispatchAttemptStatus.PENDING,
                or_(
                    col(TaskDispatchAttempt.expires_at).is_(None),
                    col(TaskDispatchAttempt.expires_at) > now,
                ),
            )
            .order_by(desc(col(TaskDispatchAttempt.pinged_at)))
            .limit(1)
        )

        result = await attempt_repo.execute(stmt)
        row = result.first()

        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active pending dispatch found for this provider.",
            )

        attempt, provider_user = row
        data = TaskDispatchAttemptResponse.model_validate(attempt)
        if provider_user:
            data.provider = MinimalProviderResponse.model_validate(provider_user)

        await system_logger.metric(
            "get_current_dispatch",
            timer.stop(),
            source="assignments.get_current_dispatch",
        )
        return BaseAPIResponse[TaskDispatchAttemptResponse](
            data=data,
            detail="Current dispatch retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn(
            "get_current_dispatch failed",
            source="assignments.get_current_dispatch",
            metadata={"detail": e.detail if hasattr(e, "detail") else str(e)},
        )
        raise
    except Exception as e:
        await system_logger.error(
            f"get_current_dispatch error: {str(e)}",
            source="assignments.get_current_dispatch",
        )
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving the current dispatch.",
        )


@router.post(
    "/tasks/{task_id}/dispatch/respond",
    response_model=BaseAPIResponse[None],
    status_code=status.HTTP_202_ACCEPTED,
)
async def respond_to_dispatch_ping(
    task_id: str,
    body: DispatchRespondBody,
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_type=UserType.PROVIDER,
            required_phone_verified=True,
            required_email_verified=True,
        )
    ),
    dispatch_service: DispatchEventService = Depends(get_dispatch_event_service),
    system_logger: LoggerService = Depends(get_logger_service),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
):
    """Provider accepts or declines a dispatch ping for a task.

    Allowed values for `status`: `accepted`, `declined`.
    A TIMEOUT or CANCELED status is not a valid provider-initiated response.
    """
    try:
        timer = Timer()
        timer.start()
        allowed = {DispatchAttemptStatus.ACCEPTED, DispatchAttemptStatus.DECLINED}
        if body.status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid response status. Allowed values: {[s.value for s in allowed]}",
            )

        task = await task_repo.get(task_id)
        if task and task.status == TaskStatus.ASSIGNED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Task has already been assigned to "
                    f"{task.assigned_provider_id or 'another provider'}"
                    + ("." if task.assigned_provider_id == current_user.id else ".")
                ),
            )

        await dispatch_service.handle_ping_response(
            task_id=task_id,
            provider_id=current_user.id,
            response_status=body.status,
        )

        action = (
            "accepted" if body.status == DispatchAttemptStatus.ACCEPTED else "declined"
        )
        await system_logger.metric(
            "respond_to_dispatch_ping",
            timer.stop(),
            source="assignments.respond_to_dispatch_ping",
        )
        return BaseAPIResponse[None](
            data=None,
            detail=f"Task dispatch {action} successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn(
            "respond_to_dispatch_ping failed",
            source="assignments.respond_to_dispatch_ping",
            metadata={"detail": str(e.detail) if hasattr(e, "detail") else str(e)},
        )
        raise
    except Exception as e:
        await system_logger.error(
            f"respond_to_dispatch_ping error: {str(e)}",
            source="assignments.respond_to_dispatch_ping",
        )
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing dispatch response.",
        )


@router.post(
    "/tasks/{task_id}/start",
    response_model=BaseAPIResponse[None],
    status_code=status.HTTP_200_OK,
)
async def start_task(
    task_id: str,
    body: PinBody,
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_type=UserType.PROVIDER,
            required_phone_verified=True,
            required_email_verified=True,
            allowed_kyc_statuses=[KYCStatus.VERIFIED],
        )
    ),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    assignment_repo: Repository[TaskAssignment] = Depends(
        GetRepository(TaskAssignment)
    ),
    system_logger: LoggerService = Depends(get_logger_service),
):
    """Provider confirms on-site arrival and starts a task using the customer's start PIN.

    The task must be in ASSIGNED status and the caller must be the assigned provider.
    On success, both task and assignment transition to IN_PROGRESS.
    """
    try:
        timer = Timer()
        timer.start()
        task = await task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found.",
            )
        assignment = task.assignment
        if not assignment:
            raise HTTPException(status_code=404, detail="Task assignment not found")

        if assignment.provider_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not the assigned provider for this task.",
            )

        if task.status != TaskStatus.ASSIGNED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Task cannot be started from status '{task.status.value}'. Expected 'assigned'.",
            )

        if task.start_pin != body.pin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid start PIN.",
            )

        now = lagos_now()

        # Update task status
        task.status = TaskStatus.IN_PROGRESS
        await task_repo.add(task)

        # Update assignment status and record start time
        stmt = select(TaskAssignment).where(TaskAssignment.task_id == task_id)
        assignment: Optional[TaskAssignment] = (
            await assignment_repo.execute(stmt)
        ).one_or_none()
        if assignment:
            assignment.status = TaskAssignmentStatus.IN_PROGRESS
            assignment.started_at = now
            await assignment_repo.add(assignment)

        await system_logger.metric(
            "start_task", timer.stop(), source="assignments.start_task"
        )
        return BaseAPIResponse[None](
            data=None,
            detail="Task started successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn(
            "start_task failed",
            source="assignments.start_task",
            metadata={"detail": str(e.detail) if hasattr(e, "detail") else str(e)},
        )
        raise
    except Exception as e:
        await system_logger.error(
            f"start_task error: {str(e)}", source="assignments.start_task"
        )
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while starting the task.",
        )


@router.post(
    "/tasks/{task_id}/complete",
    response_model=BaseAPIResponse[None],
    status_code=status.HTTP_202_ACCEPTED,
)
async def complete_task(
    task_id: str,
    body: TaskCompleteBody,
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_type=UserType.PROVIDER,
            required_phone_verified=True,
            required_email_verified=True,
        )
    ),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    system_logger: LoggerService = Depends(get_logger_service),
):
    """Provider finalises a task using the customer's completion PIN and selects payment_mode (cash or online).

    The task must be IN_PROGRESS and the caller must be the assigned provider.
    On success, completion and payment settlement are processed asynchronously by a Celery worker.
    """
    try:
        timer = Timer()
        timer.start()
        task = await task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found.",
            )
        assignment = task.assignment
        if not assignment:
            raise HTTPException(status_code=404, detail="Task assignment not found")

        if assignment.provider_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not the assigned provider for this task.",
            )

        if task.status != TaskStatus.IN_PROGRESS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Task cannot be completed from status '{task.status.value}'. Expected 'in_progress'.",
            )

        if task.completion_pin != body.pin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid completion PIN.",
            )

        # Enqueue heavy finalisation & payment settlement to Celery worker

        # pyrefly: ignore [not-callable]
        complete_task_assignment.delay(
            task_id,
            current_user.id,
            payment_mode=body.payment_mode.value,
        )  # type: ignore

        await system_logger.metric(
            "complete_task", timer.stop(), source="assignments.complete_task"
        )
        return BaseAPIResponse[None](
            data=None,
            detail="Task completion confirmed — finalising in background.",
            status_code=status.HTTP_202_ACCEPTED,
        )
    except HTTPException as e:
        await system_logger.warn(
            "complete_task failed",
            source="assignments.complete_task",
            metadata={"detail": str(e.detail) if hasattr(e, "detail") else str(e)},
        )
        raise
    except Exception as e:
        await system_logger.error(
            f"complete_task error: {str(e)}", source="assignments.complete_task"
        )
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while completing the task.",
        )


@router.post(
    "/tasks/{task_id}/verify-provider",
    response_model=BaseAPIResponse[MinimalProviderResponse],
    status_code=status.HTTP_200_OK,
)
async def verify_provider_pin(
    task_id: str,
    body: PinBody,
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_type=UserType.CUSTOMER,
            required_phone_verified=True,
            required_email_verified=True,
        )
    ),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    user_repo: Repository[User] = Depends(GetRepository(User)),
    system_logger: LoggerService = Depends(get_logger_service),
):
    """Customer verifies the assigned provider's identity using the assignment PIN given by the provider.

    Returns MinimalProviderResponse if verified. Otherwise, returns HTTP 400 with security warning.
    """
    try:
        timer = Timer()
        timer.start()
        task = await task_repo.get(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found.",
            )

        if task.customer_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to verify provider for this task.",
            )

        assignment = task.assignment
        if not assignment or not assignment.provider_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No assigned provider found for this task.",
            )

        if not assignment.pin or assignment.pin != body.pin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification failed: We do not know who this person is and the PIN does not match. Do not allow them entry or contact them, as they may be an imposter.",
            )

        provider_user = await user_repo.get(assignment.provider_id)
        if not provider_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assigned provider user profile not found.",
            )

        provider_data = MinimalProviderResponse.from_user(provider_user)

        await system_logger.metric(
            "verify_provider_pin",
            timer.stop(),
            source="assignments.verify_provider_pin",
        )
        return BaseAPIResponse[MinimalProviderResponse](
            data=provider_data,
            detail="Provider identity verified successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn(
            "verify_provider_pin failed",
            source="assignments.verify_provider_pin",
            metadata={"detail": e.detail if hasattr(e, "detail") else str(e)},
        )
        raise
    except Exception as e:
        await system_logger.error(
            f"verify_provider_pin error: {str(e)}",
            source="assignments.verify_provider_pin",
        )
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while verifying the provider PIN.",
        )
