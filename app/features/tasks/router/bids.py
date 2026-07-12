from fastapi import APIRouter, Depends, status, HTTPException
from typing import List

from app.core.api_response import BaseAPIResponse
from app.core.deps import GetCurrentUser
from app.features.users.schemas import UserResponse
from app.core.models.users import UserType
from app.features.tasks.schemas import TaskBidCreate, TaskBidResponse, TaskAssignmentResponse
from app.features.tasks.services import TaskService, get_task_service
from app.core.error_handler import AppErrorHandler

router = APIRouter(tags=["Bids"])

# Custom dependency for provider authentication
get_current_provider = GetCurrentUser(required_type=UserType.PROVIDER)

@router.post("/tasks/{task_id}/bids", response_model=BaseAPIResponse[TaskBidResponse], status_code=status.HTTP_201_CREATED)
async def create_bid(
    task_id: str,
    schema: TaskBidCreate,
    current_provider: UserResponse = Depends(get_current_provider),
    task_service: TaskService = Depends(get_task_service)
):
    """Submit a bid for a task (restricted to provider profiles)."""
    try:
        bid = await task_service.create_bid(task_id, current_provider.id, schema)
        return BaseAPIResponse[TaskBidResponse](
            data=TaskBidResponse.model_validate(bid),
            detail="Bid submitted successfully.",
            status_code=status.HTTP_201_CREATED
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while submitting the bid."
        )

@router.get("/tasks/{task_id}/bids", response_model=BaseAPIResponse[List[TaskBidResponse]], status_code=status.HTTP_200_OK)
async def get_task_bids(
    task_id: str,
    current_user: UserResponse = Depends(GetCurrentUser()),
    task_service: TaskService = Depends(get_task_service)
):
    """Retrieve all bids for a task. Customers see all bids; providers see only their own."""
    try:
        bids = await task_service.get_task_bids(task_id, current_user.id, current_user.type)
        return BaseAPIResponse[List[TaskBidResponse]](
            data=[TaskBidResponse.model_validate(b) for b in bids],
            detail="Bids retrieved successfully.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving bids."
        )

@router.post("/bids/{bid_id}/withdraw", response_model=BaseAPIResponse[TaskBidResponse], status_code=status.HTTP_200_OK)
async def withdraw_bid(
    bid_id: str,
    current_provider: UserResponse = Depends(get_current_provider),
    task_service: TaskService = Depends(get_task_service)
):
    """Withdraw an active bid."""
    try:
        bid = await task_service.withdraw_bid(bid_id, current_provider.id)
        return BaseAPIResponse[TaskBidResponse](
            data=TaskBidResponse.model_validate(bid),
            detail="Bid withdrawn successfully.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while withdrawing the bid."
        )

@router.post("/bids/{bid_id}/accept", response_model=BaseAPIResponse[TaskAssignmentResponse], status_code=status.HTTP_200_OK)
async def accept_bid(
    bid_id: str,
    current_user: UserResponse = Depends(GetCurrentUser()),
    task_service: TaskService = Depends(get_task_service)
):
    """Accept a bid and assign the task to the provider (restricted to the task customer)."""
    try:
        assignment = await task_service.accept_bid(bid_id, current_user.id)
        return BaseAPIResponse[TaskAssignmentResponse](
            data=TaskAssignmentResponse.model_validate(assignment),
            detail="Bid accepted and provider assigned successfully.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while accepting the bid."
        )
