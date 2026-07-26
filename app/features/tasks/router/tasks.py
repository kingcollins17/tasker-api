from app.core.utils.timer import Timer
from app.core.services.logger_service import LoggerService, get_logger_service
from app.core.deps import GetCurrentUserOrAdminOptional
from app.core.models.users import UserType
from typing import Optional, Union, List
from datetime import datetime
from app.core.models.admins import AdminUser
from app.core.services.storage import StorageService, get_storage_service
from fastapi import (
    APIRouter,
    Depends,
    Query,
    status,
    HTTPException,
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
from app.core.schemas.users import MinimalProviderResponse, MinimalCustomerResponse
from app.features.tasks.schemas import (
    TaskCreate,
    TaskPriceEstimateRequest,
    TaskUpdate,
    TaskResponse,
    TaskLocationUpdate,
    TaskLocationResponse,
    TaskAttachmentResponse,
    TaskListResponse,
)
from app.features.services.pricing_engine import PricingBreakdown

from app.core.models.tasks import TaskAttachment, Task
from app.features.tasks.services import TaskService, get_task_service
from app.core.error_handler import AppErrorHandler
from app.core.models.tasks import TaskStatus
from app.core.queries.task_queries import TaskQueries
from app.core.repository import Repository, GetRepository

from app.features.tasks.celery.dispatch import start_dispatch_workflow

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.post(
    "",
    response_model=BaseAPIResponse[TaskResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_task(
    schema: TaskCreate,
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_email_verified=True,
            required_phone_verified=True,
            required_type=UserType.CUSTOMER,
        )
    ),
    task_service: TaskService = Depends(get_task_service),
    notification_service: NotificationService = Depends(get_notification_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Create a new task with spatial location coordinates."""
    try:
        timer = Timer()
        timer.start()
        task = await task_service.create_task(current_user.id, schema)

        # pyrefly: ignore [not-callable]
        start_dispatch_workflow.delay(task.id)  

        await system_logger.metric('create_task', timer.stop(), source='tasks.create_task')
        return BaseAPIResponse[TaskResponse](
            data=TaskResponse.model_validate(task),
            detail="Task created successfully.",
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException as e:
        await system_logger.warn('create_task failed', source='tasks.create_task', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'create_task error: {str(e)}', source='tasks.create_task')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating the task.",
        )


@router.post(
    "/price-breakdown",
    response_model=BaseAPIResponse[PricingBreakdown],
    status_code=status.HTTP_200_OK,
)
async def get_task_price_breakdown(
    schema: TaskPriceEstimateRequest,
    current_user: Optional[Union[UserResponse, AdminUser]] = Depends(GetCurrentUserOrAdminOptional()),
    task_service: TaskService = Depends(get_task_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Calculate and return upfront price breakdown for a task request before posting."""
    try:
        timer = Timer()
        timer.start()
        user_id = current_user.id if current_user and hasattr(current_user, "id") else None
        breakdown = await task_service.estimate_task_price(schema, customer_id=user_id)
        await system_logger.metric('get_task_price_breakdown', timer.stop(), source='tasks.get_task_price_breakdown')
        return BaseAPIResponse[PricingBreakdown](
            data=breakdown,
            detail="Price breakdown calculated successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('get_task_price_breakdown failed', source='tasks.get_task_price_breakdown', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'get_task_price_breakdown error: {str(e)}', source='tasks.get_task_price_breakdown')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while calculating price breakdown.",
        )


@router.get(
    "",
    response_model=BaseAPIResponse[PaginatedData[TaskListResponse]],
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
    scheduled_start_at: Optional[datetime] = Query(None),
    expires_at: Optional[datetime] = Query(None),
    customer_id: Optional[str] = Query(None),
    task_service: TaskService = Depends(get_task_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Retrieve a list of tasks matching the filters and coordinates."""
    try:
        timer = Timer()
        timer.start()
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
            scheduled_start_at=scheduled_start_at,
            expires_at=expires_at,
            customer_id=customer_id,
        )
        data = PaginatedData[TaskListResponse](
            items=[TaskListResponse.model_validate(t) for t in tasks],
            total=total,
            page=page,
            per_page=per_page,
        )
        await system_logger.metric('list_tasks', timer.stop(), source='tasks.list_tasks')
        return BaseAPIResponse[PaginatedData[TaskListResponse]](
            data=data,
            detail="Tasks retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('list_tasks failed', source='tasks.list_tasks', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'list_tasks error: {str(e)}', source='tasks.list_tasks')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while listing tasks.",
        )


@router.get(
    "/active",
    response_model=BaseAPIResponse[PaginatedData[TaskListResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_active_tasks(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    category_id: Optional[str] = Query(None),
    service_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: str = Query("scheduled_start_at"),
    sort_desc: bool = Query(True),
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.CUSTOMER)
    ),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Retrieve a list of active tasks (assigned or in progress) for the signed in customer."""
    try:
        timer = Timer()
        timer.start()
        stmt, count_stmt = TaskQueries.get_customer_tasks_query(
            customer_id=current_user.id,
            statuses=[TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS],
            category_id=category_id,
            service_id=service_id,
            search=search,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )

        count_result = await task_repo.execute(count_stmt)
        total = count_result.first() or 0

        stmt = stmt.offset((page - 1) * per_page).limit(per_page)
        results = await task_repo.execute(stmt)
        tasks = list(results.scalars().all())

        items = [TaskListResponse.model_validate(t) for t in tasks]

        data = PaginatedData[TaskListResponse](
            items=items,
            total=total,
            page=page,
            per_page=per_page,
        )
        await system_logger.metric('list_active_tasks', timer.stop(), source='tasks.list_active_tasks')
        return BaseAPIResponse[PaginatedData[TaskListResponse]](
            data=data,
            detail="Active tasks retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('list_active_tasks failed', source='tasks.list_active_tasks', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'list_active_tasks error: {str(e)}', source='tasks.list_active_tasks')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while fetching active tasks.",
        )


@router.get(
    "/pending",
    response_model=BaseAPIResponse[PaginatedData[TaskListResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_pending_tasks(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    category_id: Optional[str] = Query(None),
    service_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: str = Query("scheduled_start_at"),
    sort_desc: bool = Query(True),
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.CUSTOMER)
    ),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Retrieve a list of pending tasks (open, matching, or searching) for the signed in customer."""
    try:
        timer = Timer()
        timer.start()
        stmt, count_stmt = TaskQueries.get_customer_tasks_query(
            customer_id=current_user.id,
            statuses=[TaskStatus.OPEN, TaskStatus.SEARCHING],
            category_id=category_id,
            service_id=service_id,
            search=search,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )

        count_result = await task_repo.execute(count_stmt)
        total = count_result.first() or 0

        stmt = stmt.offset((page - 1) * per_page).limit(per_page)
        results = await task_repo.execute(stmt)
        tasks = list(results.scalars().all())

        items = [TaskListResponse.model_validate(t) for t in tasks]

        data = PaginatedData[TaskListResponse](
            items=items,
            total=total,
            page=page,
            per_page=per_page,
        )
        await system_logger.metric('list_pending_tasks', timer.stop(), source='tasks.list_pending_tasks')
        return BaseAPIResponse[PaginatedData[TaskListResponse]](
            data=data,
            detail="Pending tasks retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('list_pending_tasks failed', source='tasks.list_pending_tasks', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'list_pending_tasks error: {str(e)}', source='tasks.list_pending_tasks')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while fetching pending tasks.",
        )


@router.get(
    "/{task_id}",
    response_model=BaseAPIResponse[TaskResponse],
    status_code=status.HTTP_200_OK,
)
async def get_task(task_id: str, task_service: TaskService = Depends(get_task_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Retrieve details for a single task by ID."""
    try:
        timer = Timer()
        timer.start()
        task = await task_service.get_task(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task with ID {task_id} not found.",
            )
        task_data = TaskResponse.model_validate(task)
        if task.customer_id:
            customer_user = await task_service.user_repo.get(task.customer_id)
            if customer_user:
                fullname = None
                if customer_user.customer_profile:
                    first_name = customer_user.customer_profile.first_name or ""
                    last_name = customer_user.customer_profile.last_name or ""
                    fullname = f"{first_name} {last_name}".strip() or None
                gender = None
                if customer_user.provider_profile:
                    gender = customer_user.provider_profile.gender

                task_data.customer = MinimalCustomerResponse(
                    id=customer_user.id,
                    fullname=fullname,
                    email=customer_user.email,
                    phone_number=customer_user.phone_number,
                    average_ratings=customer_user.average_ratings,
                    credibility_score=customer_user.credibility_score,
                    gender=gender,
                )

        await system_logger.metric('get_task', timer.stop(), source='tasks.get_task')
        return BaseAPIResponse[TaskResponse](
            data=task_data,
            detail="Task retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('get_task failed', source='tasks.get_task', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'get_task error: {str(e)}', source='tasks.get_task')
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
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Update details for an existing task."""
    try:
        timer = Timer()
        timer.start()
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
            )

        is_admin = isinstance(current_user, AdminUser)
        task = await task_service.update_task(
            task_id, current_user.id, schema, is_admin=is_admin
        )
        await system_logger.metric('update_task', timer.stop(), source='tasks.update_task')
        return BaseAPIResponse[TaskResponse](
            data=TaskResponse.model_validate(task),
            detail="Task updated successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('update_task failed', source='tasks.update_task', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'update_task error: {str(e)}', source='tasks.update_task')
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
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Cancel/Delete a task."""
    try:
        timer = Timer()
        timer.start()
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
            )

        is_admin = isinstance(current_user, AdminUser)
        success = await task_service.delete_task(
            task_id, current_user.id, is_admin=is_admin
        )
        await system_logger.metric('delete_task', timer.stop(), source='tasks.delete_task')
        return BaseAPIResponse[bool](
            data=success,
            detail="Task cancelled successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('delete_task failed', source='tasks.delete_task', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'delete_task error: {str(e)}', source='tasks.delete_task')
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
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Update a task's location."""
    try:
        timer = Timer()
        timer.start()
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

        await system_logger.metric('update_task_location', timer.stop(), source='tasks.update_task_location')
        return BaseAPIResponse[TaskLocationResponse](
            data=TaskLocationResponse.model_validate(location),
            detail="Task location updated successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('update_task_location failed', source='tasks.update_task_location', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'update_task_location error: {str(e)}', source='tasks.update_task_location')
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
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Delete a task's location."""
    try:
        timer = Timer()
        timer.start()
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
        await system_logger.metric('delete_task_location', timer.stop(), source='tasks.delete_task_location')
        return BaseAPIResponse[bool](
            data=True,
            detail="Task location deleted successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('delete_task_location failed', source='tasks.delete_task_location', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'delete_task_location error: {str(e)}', source='tasks.delete_task_location')
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
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Upload a file attachment for a task."""
    try:
        timer = Timer()
        timer.start()
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

        await system_logger.metric('upload_task_attachment', timer.stop(), source='tasks.upload_task_attachment')
        return BaseAPIResponse[TaskAttachmentResponse](
            data=TaskAttachmentResponse.model_validate(attachment),
            detail="Attachment uploaded successfully.",
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException as e:
        await system_logger.warn('upload_task_attachment failed', source='tasks.upload_task_attachment', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'upload_task_attachment error: {str(e)}', source='tasks.upload_task_attachment')
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
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Delete a task attachment."""
    try:
        timer = Timer()
        timer.start()
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

        await system_logger.metric('delete_task_attachment', timer.stop(), source='tasks.delete_task_attachment')
        return BaseAPIResponse[bool](
            data=True,
            detail="Task attachment deleted successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('delete_task_attachment failed', source='tasks.delete_task_attachment', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'delete_task_attachment error: {str(e)}', source='tasks.delete_task_attachment')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting the attachment.",
        )


@router.get(
    "/{task_id}/nearby-providers",
    response_model=BaseAPIResponse[List[MinimalProviderResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_nearby_providers(
    task_id: str,
    radius_km: float = Query(10.0, ge=0.1, description="Radius in kilometers"),
    current_user: UserResponse = Depends(GetCurrentUser()),
    task_service: TaskService = Depends(get_task_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Fetch providers within a specific radius of a task."""
    try:
        timer = Timer()
        timer.start()
        users = await task_service.get_providers_near_task(task_id, radius_km)

        providers = []
        for u in users:
            fullname = None
            gender = None
            if u.provider_profile:
                first_name = u.provider_profile.first_name or ""
                last_name = u.provider_profile.last_name or ""
                fullname = f"{first_name} {last_name}".strip() or None
                gender = u.provider_profile.gender

            providers.append(
                MinimalProviderResponse(
                    id=u.id,
                    email=u.email,
                    fullname=fullname,
                    average_ratings=u.average_ratings,
                    credibility_score=u.credibility_score,
                    gender=gender,
                )
            )

        await system_logger.metric('get_nearby_providers', timer.stop(), source='tasks.get_nearby_providers')
        return BaseAPIResponse[List[MinimalProviderResponse]](
            data=providers,
            detail="Nearby providers fetched successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('get_nearby_providers failed', source='tasks.get_nearby_providers', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise
    except Exception as e:
        await system_logger.error(f'get_nearby_providers error: {str(e)}', source='tasks.get_nearby_providers')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while fetching nearby providers.",
        )
