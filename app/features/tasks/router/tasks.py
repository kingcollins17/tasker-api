from app.core.deps import GetCurrentUserOrAdminOptional
from app.core.models.users import UserType
from typing import Optional, Union
from datetime import datetime
from app.core.models.admins import AdminUser
from app.core.services.storage import StorageService, get_storage_service
from fastapi import (
    APIRouter,
    Depends,
    Query,
    status,
    HTTPException,
    BackgroundTasks,
    UploadFile,
    File,
)
from app.features.notifications.services import (
    NotificationService,
    get_notification_service,
)
from app.features.notifications.schemas import CreateNotification
from app.core.models.notifications import NotificationType
from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.deps import (
    GetCurrentUser,
)
from app.features.users.schemas import UserResponse
from app.features.tasks.schemas import (
    TaskCreate,
    TaskUpdate,
    TaskResponse,
    TaskLocationUpdate,
    TaskLocationResponse,
    TaskAttachmentResponse,
)

from app.core.models.tasks import TaskAttachment
from app.features.tasks.services import TaskService, get_task_service
from app.core.error_handler import AppErrorHandler
from app.core.models.tasks import TaskStatus
from app.features.tasks.celery_tasks import process_new_task_workflow

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.post(
    "",
    response_model=BaseAPIResponse[TaskResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_task(
    schema: TaskCreate,
    background_tasks: BackgroundTasks,
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_email_verified=True,
            required_phone_verified=True,
            required_type=UserType.CUSTOMER,
        )
    ),
    task_service: TaskService = Depends(get_task_service),
    notification_service: NotificationService = Depends(get_notification_service),
):
    """Create a new task with spatial location coordinates."""
    try:
        task = await task_service.create_task(current_user.id, schema)

        background_tasks.add_task(
            notification_service.create_notification,
            schema=CreateNotification(
                type=NotificationType.SECURITY_ALERT,
                title="Task Created Successfully",
                body=f"Your task '{task.title}' has been created. Your start pin is {task.start_pin} and completion pin is {task.completion_pin}.",
                recipient_ids=[current_user.id],
            ),
            created_by=current_user.id,
        )

        # pyrefly: ignore [bad-argument-type]
        background_tasks.add_task(process_new_task_workflow.delay, task.id)

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
    region_id: Optional[str] = Query(None),
    budget_min: Optional[float] = Query(None, ge=0.0),
    budget_max: Optional[float] = Query(None, ge=0.0),
    scheduled_start_at: Optional[datetime] = Query(None),
    expires_at: Optional[datetime] = Query(None),
    customer_id: Optional[str] = Query(None),
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
            region_id=region_id,
            budget_min=budget_min,
            budget_max=budget_max,
            scheduled_start_at=scheduled_start_at,
            expires_at=expires_at,
            customer_id=customer_id,
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
    current_user: Union[UserResponse, AdminUser, None] = Depends(
        GetCurrentUserOrAdminOptional
    ),
    task_service: TaskService = Depends(get_task_service),
):
    """Update details for an existing task."""
    try:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
            )

        is_admin = isinstance(current_user, AdminUser)
        task = await task_service.update_task(
            task_id, current_user.id, schema, is_admin=is_admin
        )
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
    current_user: Union[UserResponse, AdminUser, None] = Depends(
        GetCurrentUserOrAdminOptional
    ),
    task_service: TaskService = Depends(get_task_service),
):
    """Cancel/Delete a task."""
    try:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
            )

        is_admin = isinstance(current_user, AdminUser)
        success = await task_service.delete_task(
            task_id, current_user.id, is_admin=is_admin
        )
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


@router.put(
    "/{task_id}/locations/{location_id}",
    response_model=BaseAPIResponse[TaskLocationResponse],
    status_code=status.HTTP_200_OK,
)
async def update_task_location(
    task_id: str,
    location_id: str,
    schema: TaskLocationUpdate,
    current_user: Union[UserResponse, AdminUser, None] = Depends(
        GetCurrentUserOrAdminOptional
    ),
    task_service: TaskService = Depends(get_task_service),
):
    """Update a task's location."""
    try:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
            )

        task = await task_service.get_task(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )

        if isinstance(current_user, UserResponse):
            if task.customer_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to update this task",
                )

        location = await task_service.location_repo.get(location_id)
        if not location or location.task_id != task_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )

        updates = schema.model_dump(exclude_unset=True)

        if "latitude" in updates or "longitude" in updates:
            lat = updates.get("latitude", location.latitude)
            lng = updates.get("longitude", location.longitude)
            updates["geography_point"] = f"POINT({lng} {lat})"

        if updates:
            location = await task_service.location_repo.update(location_id, updates)

        return BaseAPIResponse[TaskLocationResponse](
            data=TaskLocationResponse.model_validate(location),
            detail="Task location updated successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating the location.",
        )


@router.delete(
    "/{task_id}/locations/{location_id}",
    response_model=BaseAPIResponse[bool],
    status_code=status.HTTP_200_OK,
)
async def delete_task_location(
    task_id: str,
    location_id: str,
    current_user: Union[UserResponse, AdminUser, None] = Depends(
        GetCurrentUserOrAdminOptional
    ),
    task_service: TaskService = Depends(get_task_service),
):
    """Delete a task's location."""
    try:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
            )

        task = await task_service.get_task(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )

        if isinstance(current_user, UserResponse):
            if task.customer_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized"
                )

        location = await task_service.location_repo.get(location_id)
        if not location or location.task_id != task_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )

        await task_service.location_repo.delete(location_id)
        return BaseAPIResponse[bool](
            data=True,
            detail="Task location deleted successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting the location.",
        )


@router.post(
    "/{task_id}/attachments",
    response_model=BaseAPIResponse[TaskAttachmentResponse],
    status_code=status.HTTP_201_CREATED,
)
async def upload_task_attachment(
    task_id: str,
    file: UploadFile = File(...),
    current_user: Union[UserResponse, AdminUser, None] = Depends(
        GetCurrentUserOrAdminOptional
    ),
    task_service: TaskService = Depends(get_task_service),
    storage_service: StorageService = Depends(get_storage_service),
):
    """Upload a file attachment for a task."""
    try:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
            )

        task = await task_service.get_task(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )

        if isinstance(current_user, UserResponse):
            if task.customer_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to upload attachments for this task",
                )

        file_url = await storage_service.upload_file(file, file.filename)

        attachment = TaskAttachment(
            task_id=task_id,
            storage_key=file_url,
            file_name=file.filename,
            file_size=file.size or 0,
            mime_type=file.content_type,
            url=file_url,
            type="file",
        )
        attachment = await task_service.attachment_repo.add(attachment)

        return BaseAPIResponse[TaskAttachmentResponse](
            data=TaskAttachmentResponse.model_validate(attachment),
            detail="Attachment uploaded successfully.",
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while uploading the attachment.",
        )


@router.delete(
    "/{task_id}/attachments/{attachment_id}",
    response_model=BaseAPIResponse[bool],
    status_code=status.HTTP_200_OK,
)
async def delete_task_attachment(
    task_id: str,
    attachment_id: str,
    current_user: UserResponse = Depends(GetCurrentUser()),
    task_service: TaskService = Depends(get_task_service),
    storage_service: StorageService = Depends(get_storage_service),
):
    """Delete a task attachment."""
    try:
        task = await task_service.get_task(task_id)
        if not task or task.customer_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized"
            )

        attachment = await task_service.attachment_repo.get(attachment_id)
        if not attachment or attachment.task_id != task_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found"
            )

        await storage_service.delete_file(attachment.storage_key)

        await task_service.attachment_repo.delete(attachment_id)

        return BaseAPIResponse[bool](
            data=True,
            detail="Task attachment deleted successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting the attachment.",
        )
