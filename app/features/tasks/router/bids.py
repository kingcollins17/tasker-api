from app.core.models.notifications import NotificationChannel
from fastapi import APIRouter, Depends, status, HTTPException, Query, BackgroundTasks
from typing import List, Optional
from sqlmodel import select, func
from sqlalchemy import desc

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.deps import GetCurrentUser
from app.features.users.schemas import UserResponse
from app.core.models.users import UserType, User
from app.core.models.tasks import TaskBid, TaskBidStatus, TaskStatus, Task
from app.core.models.notifications import NotificationType, NotificationPriority
from app.core.repository import GetRepository, Repository
from app.features.tasks.schemas import (
    TaskBidCreate,
    TaskBidUpdate,
    TaskBidResponse,
    TaskAssignmentResponse,
    TaskBidWithTaskResponse,
    TaskMinimalResponse,
    TaskBidWithProviderResponse,
)
from app.core.schemas.users import MinimalProviderResponse
from app.features.tasks.services import TaskService, get_task_service
from app.features.notifications.services import (
    NotificationService,
    get_notification_service,
)
from app.core.utils.datetime_helper import utc_now

from app.features.notifications.schemas import CreateNotification
from app.core.error_handler import AppErrorHandler

router = APIRouter(tags=["Bids"])

# Custom dependency for provider authentication
get_current_provider = GetCurrentUser(required_type=UserType.PROVIDER)


@router.post(
    "/tasks/{task_id}/bids",
    response_model=BaseAPIResponse[TaskBidResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_bid(
    task_id: str,
    schema: TaskBidCreate,
    background_tasks: BackgroundTasks,
    current_provider: UserResponse = Depends(get_current_provider),
    task_service: TaskService = Depends(get_task_service),
    notification_service: NotificationService = Depends(get_notification_service),
):
    """Submit a bid for a task (restricted to provider profiles)."""
    try:
        bid = await task_service.create_bid(task_id, current_provider.id, schema)

        task = await task_service.get_task(task_id)
        if task and task.customer_id:
            notification_schema = CreateNotification(
                type=NotificationType.NEW_MESSAGE,
                title="New Bid Received",
                body="A new bid has been placed on your task.",
                data={"task_id": task_id, "bid_id": bid.id},
                recipient_ids=[task.customer_id],
                priority=NotificationPriority.NORMAL,
                channels=[
                    NotificationChannel.EMAIL,
                    NotificationChannel.PUSH,
                    NotificationChannel.IN_APP,
                ],
            )
            background_tasks.add_task(
                notification_service.create_notification,
                schema=notification_schema,
                created_by=current_provider.id,
            )

        return BaseAPIResponse[TaskBidResponse](
            data=TaskBidResponse.model_validate(bid),
            detail="Bid submitted successfully.",
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while submitting the bid.",
        )


@router.get(
    "/bids",
    response_model=BaseAPIResponse[PaginatedData[TaskBidWithTaskResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_my_bids(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status_filter: Optional[TaskBidStatus] = Query(None, alias="status"),
    task_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: str = Query("created_at"),
    sort_desc: bool = Query(True),
    current_provider: UserResponse = Depends(get_current_provider),
    bid_repo: Repository[TaskBid] = Depends(GetRepository(TaskBid)),
):
    """Retrieve a paginated list of bids submitted by the current user."""
    try:
        query = (
            select(TaskBid, Task)
            .join(Task, TaskBid.task_id == Task.id)
            .where(TaskBid.provider_id == current_provider.id)
        )

        if status_filter:
            query = query.where(TaskBid.status == status_filter)
        if task_id:
            query = query.where(TaskBid.task_id == task_id)
        if search:
            query = query.where(TaskBid.message.ilike(f"%{search}%"))

        count_query = (
            select(func.count(TaskBid.id))
            .join(Task, TaskBid.task_id == Task.id)
            .where(TaskBid.provider_id == current_provider.id)
        )

        if status_filter:
            count_query = count_query.where(TaskBid.status == status_filter)
        if task_id:
            count_query = count_query.where(TaskBid.task_id == task_id)
        if search:
            count_query = count_query.where(TaskBid.message.ilike(f"%{search}%"))

        total = (await bid_repo.execute(count_query)).one()

        if hasattr(TaskBid, sort_by):
            order_column = getattr(TaskBid, sort_by)
            if sort_desc:
                query = query.order_by(desc(order_column))
            else:
                query = query.order_by(order_column)

        query = query.offset((page - 1) * per_page).limit(per_page)

        bids_result = await bid_repo.execute(query)
        bids = bids_result.unique().all()

        items = []
        for bid_model, task_model in bids:
            bid_data = TaskBidWithTaskResponse.model_validate(bid_model)
            bid_data.task = TaskMinimalResponse.model_validate(task_model)
            items.append(bid_data)

        data = PaginatedData[TaskBidWithTaskResponse](
            items=items,
            total=total,
            page=page,
            per_page=per_page,
        )
        return BaseAPIResponse[PaginatedData[TaskBidWithTaskResponse]](
            data=data,
            detail="Bids retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving bids.",
        )


@router.get(
    "/tasks/{task_id}/bids",
    response_model=BaseAPIResponse[List[TaskBidWithProviderResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_task_bids(
    task_id: str,
    sort_by: Optional[str] = Query(None, description="Field to sort by (e.g. price, created_at)"),
    sort_desc: bool = Query(True, description="Sort descending"),
    current_user: UserResponse = Depends(GetCurrentUser()),
    task_service: TaskService = Depends(get_task_service),
    bid_repo: Repository[TaskBid] = Depends(GetRepository(TaskBid)),
):
    """Retrieve all bids for a task. Customers see all bids; providers see only their own."""
    try:
        task = await task_service.get_task(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )

        query = (
            select(TaskBid, User)
            .join(User, TaskBid.provider_id == User.id)
            .where(TaskBid.task_id == task_id)
        )

        if task.customer_id == current_user.id:
            pass  # Can see all bids
        elif current_user.type == UserType.PROVIDER:
            query = query.where(TaskBid.provider_id == current_user.id)
        else:
            query = query.where(TaskBid.id == "0")

        if sort_by and hasattr(TaskBid, sort_by):
            order_column = getattr(TaskBid, sort_by)
            if sort_desc:
                query = query.order_by(desc(order_column))
            else:
                query = query.order_by(order_column)
        else:
            # Default sorting for bids: best providers ranked first
            query = query.order_by(desc(User.average_ratings), desc(User.credibility_score))

        results = await bid_repo.execute(query)
        
        items = []
        for bid_model, user_model in results.unique().all():
            fullname = None
            gender = None
            if user_model.provider_profile:
                first_name = user_model.provider_profile.first_name or ""
                last_name = user_model.provider_profile.last_name or ""
                fullname = f"{first_name} {last_name}".strip() or None
                gender = user_model.provider_profile.gender

            provider_info = MinimalProviderResponse(
                id=user_model.id,
                email=user_model.email,
                fullname=fullname,
                average_ratings=user_model.average_ratings,
                credibility_score=user_model.credibility_score,
                gender=gender,
            )
            bid_data = TaskBidWithProviderResponse.model_validate(bid_model)
            bid_data.provider = provider_info
            items.append(bid_data)

        return BaseAPIResponse[List[TaskBidWithProviderResponse]](
            data=items,
            detail="Bids retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving bids.",
        )


@router.post(
    "/bids/{bid_id}/withdraw",
    response_model=BaseAPIResponse[TaskBidResponse],
    status_code=status.HTTP_200_OK,
)
async def withdraw_bid(
    bid_id: str,
    current_provider: UserResponse = Depends(get_current_provider),
    task_service: TaskService = Depends(get_task_service),
):
    """Withdraw an active bid."""
    try:
        bid = await task_service.withdraw_bid(bid_id, current_provider.id)
        return BaseAPIResponse[TaskBidResponse](
            data=TaskBidResponse.model_validate(bid),
            detail="Bid withdrawn successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while withdrawing the bid.",
        )


@router.post(
    "/bids/{bid_id}/accept",
    response_model=BaseAPIResponse[TaskAssignmentResponse],
    status_code=status.HTTP_200_OK,
)
async def accept_bid(
    bid_id: str,
    current_user: UserResponse = Depends(GetCurrentUser()),
    task_service: TaskService = Depends(get_task_service),
):
    """Accept a bid and assign the task to the provider (restricted to the task customer)."""
    try:
        assignment = await task_service.accept_bid(bid_id, current_user.id)
        return BaseAPIResponse[TaskAssignmentResponse](
            data=TaskAssignmentResponse.model_validate(assignment),
            detail="Bid accepted and provider assigned successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while accepting the bid.",
        )


@router.put(
    "/bids/{bid_id}",
    response_model=BaseAPIResponse[TaskBidResponse],
    status_code=status.HTTP_200_OK,
)
async def update_bid(
    bid_id: str,
    schema: TaskBidUpdate,
    background_tasks: BackgroundTasks,
    current_provider: UserResponse = Depends(get_current_provider),
    bid_repo: Repository[TaskBid] = Depends(GetRepository(TaskBid)),
    task_service: TaskService = Depends(get_task_service),
    notification_service: NotificationService = Depends(get_notification_service),
):
    """Update an existing bid."""
    try:
        bid = await bid_repo.get(bid_id)
        if not bid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Bid not found"
            )

        if bid.provider_id != current_provider.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to update this bid",
            )

        if bid.status != TaskBidStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Can only update pending bids",
            )

        task = await task_service.get_task(bid.task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )

        if task.status not in [TaskStatus.OPEN, TaskStatus.BIDDING]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Can only update bids for open or active bidding tasks",
            )

        update_data = schema.model_dump(exclude_unset=True)
        if update_data:

            update_data["updated_at"] = utc_now()
            bid = await bid_repo.update(bid_id, update_data)

            notification_schema = CreateNotification(
                type=NotificationType.NEW_MESSAGE,
                title="Bid Updated",
                body="A provider has updated their bid on your task.",
                data={"task_id": bid.task_id, "bid_id": bid.id},
                recipient_ids=[task.customer_id],
                priority=NotificationPriority.NORMAL,
            )
            background_tasks.add_task(
                notification_service.create_notification,
                schema=notification_schema,
                created_by=current_provider.id,
            )

        return BaseAPIResponse[TaskBidResponse](
            data=TaskBidResponse.model_validate(bid),
            detail="Bid updated successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating the bid.",
        )
