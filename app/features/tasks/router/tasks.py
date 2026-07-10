from typing import Optional
from fastapi import APIRouter, Depends, Query, status, HTTPException
from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.deps import get_current_user
from app.features.users.schemas import UserResponse
from app.features.tasks.schemas import TaskCreate, TaskUpdate, TaskResponse
from app.features.tasks.services import TaskService, get_task_service
from app.core.error_handler import AppErrorHandler
from app.core.models.tasks import TaskStatus

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.post(
    "",
    response_model=BaseAPIResponse[TaskResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_task(
    schema: TaskCreate,
    current_user: UserResponse = Depends(get_current_user),
    task_service: TaskService = Depends(get_task_service),
):
    """Create a new task with spatial location coordinates."""
    try:
        task = await task_service.create_task(current_user.id, schema)
        return BaseAPIResponse[TaskResponse](
            data=TaskResponse.model_validate(task),
            detail="Task created successfully.",
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating the task.",
        )


@router.get(
    "",
    response_model=BaseAPIResponse[PaginatedData[TaskResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_tasks(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status_filter: Optional[TaskStatus] = Query(None, alias="status"),
    category_id: Optional[str] = Query(None),
    service_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    latitude: Optional[float] = Query(None, ge=-90.0, le=90.0),
    longitude: Optional[float] = Query(None, ge=-180.0, le=180.0),
    radius_km: Optional[float] = Query(None, ge=0.0),
    sort_by: str = Query("created_at"),
    sort_desc: bool = Query(True),
    task_service: TaskService = Depends(get_task_service),
):
    """Retrieve a list of tasks matching the filters and coordinates."""
    try:
        tasks, total = await task_service.get_tasks(
            page=page,
            per_page=per_page,
            status_filter=status_filter,
            category_id=category_id,
            service_id=service_id,
            search=search,
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )
        data = PaginatedData[TaskResponse](
            items=[TaskResponse.model_validate(t) for t in tasks],
            total=total,
            page=page,
            per_page=per_page,
        )
        return BaseAPIResponse[PaginatedData[TaskResponse]](
            data=data,
            detail="Tasks retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while listing tasks.",
        )


@router.get(
    "/{task_id}",
    response_model=BaseAPIResponse[TaskResponse],
    status_code=status.HTTP_200_OK,
)
async def get_task(task_id: str, task_service: TaskService = Depends(get_task_service)):
    """Retrieve details for a single task by ID."""
    try:
        task = await task_service.get_task(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task with ID {task_id} not found.",
            )
        return BaseAPIResponse[TaskResponse](
            data=TaskResponse.model_validate(task),
            detail="Task retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving the task.",
        )


@router.put(
    "/{task_id}",
    response_model=BaseAPIResponse[TaskResponse],
    status_code=status.HTTP_200_OK,
)
async def update_task(
    task_id: str,
    schema: TaskUpdate,
    current_user: UserResponse = Depends(get_current_user),
    task_service: TaskService = Depends(get_task_service),
):
    """Update details for an existing task."""
    try:
        task = await task_service.update_task(task_id, current_user.id, schema)
        return BaseAPIResponse[TaskResponse](
            data=TaskResponse.model_validate(task),
            detail="Task updated successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating the task.",
        )


@router.delete(
    "/{task_id}", response_model=BaseAPIResponse[bool], status_code=status.HTTP_200_OK
)
async def delete_task(
    task_id: str,
    current_user: UserResponse = Depends(get_current_user),
    task_service: TaskService = Depends(get_task_service),
):
    """Cancel/Delete a task."""
    try:
        success = await task_service.delete_task(task_id, current_user.id)
        return BaseAPIResponse[bool](
            data=success,
            detail="Task cancelled successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while cancelling the task.",
        )
