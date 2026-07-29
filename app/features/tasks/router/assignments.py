from app.core.utils.timer import Timer
from app.core.services.logger_service import LoggerService, get_logger_service
from app.core.models.users import KYCStatus
from fastapi import APIRouter, Depends, status, HTTPException, Query
from typing import Optional
from pydantic import BaseModel
from sqlmodel import select, func
from sqlalchemy import desc

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
from app.features.tasks.celery.dispatch import (
    complete_task_assignment,
    process_provider_dispatch_response,
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
    current_user: UserResponse = Depends(GetCurrentUser()),
    assignment_repo: Repository[TaskAssignment] = Depends(
        GetRepository(TaskAssignment)
    ),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Retrieve a paginated list of assignments for the current user."""
    try:
        timer = Timer()
        timer.start()
        query = select(TaskAssignment, Task).join(
            # pyrefly: ignore [bad-argument-type]
            Task,
            # pyrefly: ignore [bad-argument-type]
            TaskAssignment.task_id == Task.id,
        )

        if current_user.type == UserType.PROVIDER:
            query = query.where(TaskAssignment.provider_id == current_user.id)
        elif current_user.type == UserType.CUSTOMER:
            query = query.where(Task.customer_id == current_user.id)
        else:
            # Return empty if user is not matching expected roles
            query = query.where(TaskAssignment.id == "0")

        if status_filter:
            query = query.where(TaskAssignment.status == status_filter)
        if task_id:
            query = query.where(TaskAssignment.task_id == task_id)

        # Counting records
        # pyrefly: ignore [bad-argument-type]
        count_query = select(func.count(TaskAssignment.id)).join(
            # pyrefly: ignore [bad-argument-type]
            Task,
            # pyrefly: ignore [bad-argument-type]
            TaskAssignment.task_id == Task.id,
        )
        if current_user.type == UserType.PROVIDER:
            count_query = count_query.where(
                TaskAssignment.provider_id == current_user.id
            )
        elif current_user.type == UserType.CUSTOMER:
            count_query = count_query.where(Task.customer_id == current_user.id)
        else:
            count_query = count_query.where(TaskAssignment.id == "0")

        if status_filter:
            count_query = count_query.where(TaskAssignment.status == status_filter)
        if task_id:
            count_query = count_query.where(TaskAssignment.task_id == task_id)

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
            TaskAssignmentWithTaskResponse.model_validate(
                assignment_model
            ).model_copy(
                update={
                    "task": TaskMinimalResponse.model_validate(task_model)
                    if task_model
                    else None
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
        await system_logger.metric('get_my_assignments', timer.stop(), source='assignments.get_my_assignments')
        return BaseAPIResponse[PaginatedData[TaskAssignmentWithTaskResponse]](
            data=data,
            detail="Assignments retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('get_my_assignments failed', source='assignments.get_my_assignments', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'get_my_assignments error: {str(e)}', source='assignments.get_my_assignments')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving assignments.",
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
    system_logger: LoggerService = Depends(get_logger_service)
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
                assignment_data.provider = MinimalProviderResponse.from_user(provider_user)

        await system_logger.metric('get_task_assignment', timer.stop(), source='assignments.get_task_assignment')
        return BaseAPIResponse[TaskAssignmentWithTaskResponse](
            data=assignment_data,
            detail="Task assignment retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('get_task_assignment failed', source='assignments.get_task_assignment', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'get_task_assignment error: {str(e)}', source='assignments.get_task_assignment')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving task assignment.",
        )


@router.get(
    "/tasks/{task_id}/dispatch/pending",
    response_model=BaseAPIResponse[TaskDispatchAttemptResponse],
    status_code=status.HTTP_200_OK,
)
async def get_pending_dispatch(
    task_id: str,
    current_user: UserResponse = Depends(GetCurrentUser()),
    attempt_repo: Repository[TaskDispatchAttempt] = Depends(
        GetRepository(TaskDispatchAttempt)
    ),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Retrieve the most recent pending dispatch attempt for a given task."""
    try:
        timer = Timer()
        timer.start()

        # Find the pending dispatch attempt for this task
        stmt = (
            select(TaskDispatchAttempt, User)
            # pyrefly: ignore [bad-argument-type]
            .join(User, TaskDispatchAttempt.provider_id == User.id, isouter=True)
            .where(
                TaskDispatchAttempt.task_id == task_id,
                TaskDispatchAttempt.status == DispatchAttemptStatus.PENDING
            )
            # pyrefly: ignore [bad-argument-type]
            .order_by(desc(TaskDispatchAttempt.pinged_at))
            .limit(1)
        )
        
        result = await attempt_repo.execute(stmt)
        row = result.first()

        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No pending dispatch attempt found for this task.",
            )
            
        attempt, provider_user = row

        data = TaskDispatchAttemptResponse.model_validate(attempt)
        if provider_user:
            data.provider = MinimalProviderResponse.model_validate(provider_user)

        await system_logger.metric('get_pending_dispatch', timer.stop(), source='assignments.get_pending_dispatch')
        return BaseAPIResponse[TaskDispatchAttemptResponse](
            data=data,
            detail="Pending dispatch attempt retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('get_pending_dispatch failed', source='assignments.get_pending_dispatch', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'get_pending_dispatch error: {str(e)}', source='assignments.get_pending_dispatch')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving the pending dispatch attempt.",
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
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Provider accepts or declines a dispatch ping for a task.

    Allowed values for `status`: `accepted`, `declined`.
    A TIMEOUT or CANCELED status is not a valid provider-initiated response.
    The response state transitions are processed asynchronously by a Celery worker.
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

        # pyrefly: ignore [not-callable]
        process_provider_dispatch_response.delay(
            task_id=task_id,
            provider_id=current_user.id,
            response_status=body.status.value,
        )

        action = (
            "accepted" if body.status == DispatchAttemptStatus.ACCEPTED else "declined"
        )
        await system_logger.metric('respond_to_dispatch_ping', timer.stop(), source='assignments.respond_to_dispatch_ping')
        return BaseAPIResponse[None](
            data=None,
            detail=f"Task dispatch {action} — processing in background.",
            status_code=status.HTTP_202_ACCEPTED,
        )
    except HTTPException as e:
        await system_logger.warn('respond_to_dispatch_ping failed', source='assignments.respond_to_dispatch_ping', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'respond_to_dispatch_ping error: {str(e)}', source='assignments.respond_to_dispatch_ping')
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
    system_logger: LoggerService = Depends(get_logger_service)
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

        if task.assigned_provider_id != current_user.id:
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

        await system_logger.metric('start_task', timer.stop(), source='assignments.start_task')
        return BaseAPIResponse[None](
            data=None,
            detail="Task started successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('start_task failed', source='assignments.start_task', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'start_task error: {str(e)}', source='assignments.start_task')
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
    system_logger: LoggerService = Depends(get_logger_service)
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

        if task.assigned_provider_id != current_user.id:
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
            task_id, current_user.id, payment_mode=body.payment_mode.value
        )

        await system_logger.metric('complete_task', timer.stop(), source='assignments.complete_task')
        return BaseAPIResponse[None](
            data=None,
            detail="Task completion confirmed — finalising in background.",
            status_code=status.HTTP_202_ACCEPTED,
        )
    except HTTPException as e:
        await system_logger.warn('complete_task failed', source='assignments.complete_task', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'complete_task error: {str(e)}', source='assignments.complete_task')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while completing the task.",
        )
