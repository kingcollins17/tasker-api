from fastapi import APIRouter, Depends, status, HTTPException, Query
from typing import Optional
from sqlmodel import select, func
from sqlalchemy import desc

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.deps import GetCurrentUser
from app.features.users.schemas import UserResponse
from app.core.models.users import UserType
from app.core.models.tasks import TaskAssignment, TaskAssignmentStatus, Task
from app.core.repository import GetRepository, Repository
from app.features.tasks.schemas import (
    TaskAssignmentResponse,
    TaskAssignmentWithTaskResponse,
    TaskMinimalResponse,
)
from app.core.error_handler import AppErrorHandler

router = APIRouter(tags=["Assignments"])

@router.get("/assignments", response_model=BaseAPIResponse[PaginatedData[TaskAssignmentWithTaskResponse]], status_code=status.HTTP_200_OK)
async def get_my_assignments(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status_filter: Optional[TaskAssignmentStatus] = Query(None, alias="status"),
    task_id: Optional[str] = Query(None),
    sort_by: str = Query("assigned_at"),
    sort_desc: bool = Query(True),
    current_user: UserResponse = Depends(GetCurrentUser()),
    assignment_repo: Repository[TaskAssignment] = Depends(GetRepository(TaskAssignment)),
):
    """Retrieve a paginated list of assignments for the current user."""
    try:
        query = select(TaskAssignment, Task).join(Task, TaskAssignment.task_id == Task.id)
        
        if current_user.type == UserType.PROVIDER:
            query = query.where(TaskAssignment.provider_id == current_user.id)
        elif current_user.type == UserType.CUSTOMER:
            query = query.where(Task.customer_id == current_user.id)
        else:
            query = query.where(TaskAssignment.id == "0") # Return empty if user is not matching expected roles

        if status_filter:
            query = query.where(TaskAssignment.status == status_filter)
        if task_id:
            query = query.where(TaskAssignment.task_id == task_id)

        # Counting records
        count_query = select(func.count(TaskAssignment.id)).join(Task, TaskAssignment.task_id == Task.id)
        if current_user.type == UserType.PROVIDER:
            count_query = count_query.where(TaskAssignment.provider_id == current_user.id)
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

        items = []
        for assignment_model, task_model in assignments:
            assignment_data = TaskAssignmentWithTaskResponse.model_validate(assignment_model)
            assignment_data.task = TaskMinimalResponse.model_validate(task_model)
            items.append(assignment_data)

        data = PaginatedData[TaskAssignmentWithTaskResponse](
            items=items,
            total=total,
            page=page,
            per_page=per_page,
        )
        return BaseAPIResponse[PaginatedData[TaskAssignmentWithTaskResponse]](
            data=data,
            detail="Assignments retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving assignments.",
        )


@router.get("/tasks/{task_id}/assignment", response_model=BaseAPIResponse[TaskAssignmentWithTaskResponse], status_code=status.HTTP_200_OK)
async def get_task_assignment(
    task_id: str,
    current_user: UserResponse = Depends(GetCurrentUser()),
    assignment_repo: Repository[TaskAssignment] = Depends(GetRepository(TaskAssignment)),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
):
    """Retrieve the assignment for a specific task if available."""
    try:
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
                status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found for this task"
            )

        # Authorization check: only customer of task or provider assigned can view
        if task.customer_id != current_user.id and assignment.provider_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this assignment"
            )
            
        assignment_data = TaskAssignmentWithTaskResponse.model_validate(assignment)
        assignment_data.task = TaskMinimalResponse.model_validate(task)
            
        return BaseAPIResponse[TaskAssignmentWithTaskResponse](
            data=assignment_data,
            detail="Task assignment retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving task assignment.",
        )
