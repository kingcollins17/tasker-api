from typing import Optional, List
from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlmodel import select, func, asc, desc, col
from sqlalchemy.orm import selectinload
from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.repository import GetRepository, Repository
from app.core.models.services import Service, ProviderServiceLink, ServiceCategory
from app.core.models.tasks import Task
from app.core.models.users import User
from app.core.error_handler import AppErrorHandler
from app.features.services.schemas import (
    ServiceResponse,
    ServiceAvailabilityResponse,
    CategoryResponse,
)
from app.core.schemas.users import MinimalProviderResponse
from app.features.users.schemas import UserResponse
from app.core.queries.services_queries import ServicesQueries
from app.core.services.cache import CacheService, get_cache_service

router = APIRouter(prefix="/services", tags=["Services"])


@router.get(
    "/categories/top",
    response_model=BaseAPIResponse[List[CategoryResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_top_categories(
    limit: int = Query(10, ge=1, le=50),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    category_repo: Repository[ServiceCategory] = Depends(GetRepository(ServiceCategory)),
):
    """Retrieve the top categories with the highest number of tasks."""
    try:
        statement = (
            select(Task.category_id, func.count(col(Task.id)).label("task_count"))
            .where(col(Task.category_id).is_not(None))
            .group_by(Task.category_id)
            .order_by(desc("task_count"))
            .limit(limit)
        )
        
        results = await task_repo.execute(statement)
        rows = results.all()
        
        category_ids = [row.category_id for row in rows]
        
        if not category_ids:
            return BaseAPIResponse[List[CategoryResponse]](
                data=[],
                detail="No top categories found.",
                status_code=status.HTTP_200_OK,
            )
            
        category_statement = select(ServiceCategory).where(col(ServiceCategory.id).in_(category_ids))
        categories_result = await category_repo.execute(category_statement)
        categories = categories_result.all()
        
        category_map = {cat.id: cat for cat in categories}
        sorted_categories = [category_map[cid] for cid in category_ids if cid in category_map]
        
        return BaseAPIResponse[List[CategoryResponse]](
            data=[CategoryResponse.model_validate(c) for c in sorted_categories],
            detail="Top categories retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving top categories.",
        )


@router.get(
    "/top",
    response_model=BaseAPIResponse[List[ServiceResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_top_services(
    limit: int = Query(10, ge=1, le=50),
    task_repo: Repository[Task] = Depends(GetRepository(Task)),
    service_repo: Repository[Service] = Depends(GetRepository(Service)),
):
    """Retrieve the top services with the highest number of tasks."""
    try:
        statement = (
            select(Task.service_id, func.count(col(Task.id)).label("task_count"))
            .where(col(Task.service_id).is_not(None))
            .group_by(Task.service_id)
            .order_by(desc("task_count"))
            .limit(limit)
        )
        
        results = await task_repo.execute(statement)
        rows = results.all()
        
        service_ids = [row.service_id for row in rows]
        
        if not service_ids:
            return BaseAPIResponse[List[ServiceResponse]](
                data=[],
                detail="No top services found.",
                status_code=status.HTTP_200_OK,
            )
            
        service_statement = (
            select(Service)
            .where(col(Service.id).in_(service_ids))
            .options(selectinload(Service.category))  # type: ignore
        )
        services_result = await service_repo.execute(service_statement)
        services = services_result.all()
        
        service_map = {svc.id: svc for svc in services}
        sorted_services = [service_map[sid] for sid in service_ids if sid in service_map]
        
        return BaseAPIResponse[List[ServiceResponse]](
            data=[ServiceResponse.model_validate(s) for s in sorted_services],
            detail="Top services retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving top services.",
        )


@router.get(
    "",
    response_model=BaseAPIResponse[PaginatedData[ServiceResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_services(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search by service name"),
    category_id: Optional[str] = Query(None, description="Filter by category ID"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    sort_by: Optional[str] = Query("created_at", description="Field to sort by"),
    sort_desc: bool = Query(True, description="Sort descending"),
    service_repo: Repository[Service] = Depends(GetRepository(Service)),
):
    """Retrieve a list of services with pagination, filtering, searching and sorting."""
    try:
        statement = select(Service).options(selectinload(Service.category))  # type: ignore
        count_statement = select(func.count()).select_from(Service)

        if search:
            search_filter = col(Service.name).ilike(f"%{search}%")
            statement = statement.where(search_filter)
            count_statement = count_statement.where(search_filter)

        if category_id:
            statement = statement.where(Service.category_id == category_id)
            count_statement = count_statement.where(Service.category_id == category_id)

        if is_active is not None:
            statement = statement.where(Service.is_active == is_active)
            count_statement = count_statement.where(Service.is_active == is_active)

        # Count
        total_result = await service_repo.execute(count_statement)
        total = total_result.first() or 0

        # Order
        if sort_by and hasattr(Service, sort_by):
            order_column = getattr(Service, sort_by)
            statement = statement.order_by(
                desc(order_column) if sort_desc else asc(order_column)
            )

        # Paginate
        statement = statement.offset((page - 1) * per_page).limit(per_page)

        results = await service_repo.execute(statement)
        services = results.all()

        data = PaginatedData[ServiceResponse](
            items=[ServiceResponse.model_validate(s) for s in services],
            total=total,
            page=page,
            per_page=per_page,
        )

        return BaseAPIResponse[PaginatedData[ServiceResponse]](
            data=data,
            detail="Services retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving services.",
        )


@router.get(
    "/{service_id}",
    response_model=BaseAPIResponse[ServiceResponse],
    status_code=status.HTTP_200_OK,
)
async def get_service(
    service_id: str, service_repo: Repository[Service] = Depends(GetRepository(Service))
):
    """Retrieve a single service by its ID."""
    try:
        statement = select(Service).where(Service.id == service_id).options(selectinload(Service.category))  # type: ignore
        results = await service_repo.execute(statement)
        service = results.first()

        if not service:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Service with ID {service_id} not found.",
            )

        return BaseAPIResponse[ServiceResponse](
            data=ServiceResponse.model_validate(service),
            detail="Service retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving the service.",
        )


@router.get(
    "/available/regions/{region_id}",
    response_model=BaseAPIResponse[PaginatedData[ServiceResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_available_services_in_region(
    region_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    service_repo: Repository[Service] = Depends(GetRepository(Service)),
    cache_service: CacheService = Depends(get_cache_service),
):
    """Retrieve a list of services that have active providers in a specific region."""
    try:
        cache_key = (
            f"services::available:region:{region_id}:page:{page}:per_page:{per_page}"
        )
        cached_data = await cache_service.get_json(cache_key)

        if cached_data:
            return BaseAPIResponse[PaginatedData[ServiceResponse]](
                data=PaginatedData[ServiceResponse](**cached_data),
                detail="Available services retrieved successfully from cache.",
                status_code=status.HTTP_200_OK,
            )

        statement, count_statement = (
            ServicesQueries.get_available_services_in_region_query(region_id)
        )

        total_result = await service_repo.execute(count_statement)
        total = total_result.first() or 0

        statement = statement.offset((page - 1) * per_page).limit(per_page)

        results = await service_repo.execute(statement)
        services = results.all()

        data = PaginatedData[ServiceResponse](
            items=[ServiceResponse.model_validate(s) for s in services],
            total=total,
            page=page,
            per_page=per_page,
        )

        await cache_service.set_json(
            cache_key, data.model_dump(mode="json"), expire=300
        )

        return BaseAPIResponse[PaginatedData[ServiceResponse]](
            data=data,
            detail="Available services retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving available services.",
        )


@router.get(
    "/{service_id}/available/regions/{region_id}",
    response_model=BaseAPIResponse[ServiceAvailabilityResponse],
    status_code=status.HTTP_200_OK,
)
async def check_service_availability_in_region(
    service_id: str,
    region_id: str,
    service_repo: Repository[Service] = Depends(GetRepository(Service)),
    cache_service: CacheService = Depends(get_cache_service),
):
    """Check if a specific service has any active providers in a particular region."""
    try:
        cache_key = f"services::{service_id}:available:region:{region_id}"
        cached_data = await cache_service.get_json(cache_key)

        if cached_data:
            return BaseAPIResponse[ServiceAvailabilityResponse](
                data=ServiceAvailabilityResponse(**cached_data),
                detail="Service availability retrieved successfully from cache.",
                status_code=status.HTTP_200_OK,
            )

        statement = ServicesQueries.check_service_availability_in_region_query(
            service_id, region_id
        )

        result = await service_repo.execute(statement)
        count = result.first() or 0
        is_available = count > 0

        data = ServiceAvailabilityResponse(is_available=is_available)

        await cache_service.set_json(
            cache_key, data.model_dump(mode="json"), expire=300
        )

        return BaseAPIResponse[ServiceAvailabilityResponse](
            data=data,
            detail="Service availability retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while checking service availability.",
        )


@router.get(
    "/{service_id}/providers/regions/{region_id}",
    response_model=BaseAPIResponse[PaginatedData[MinimalProviderResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_providers_for_service_in_region(
    service_id: str,
    region_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user_repo: Repository[User] = Depends(GetRepository(User)),
    cache_service: CacheService = Depends(get_cache_service),
):
    """Retrieve a paginated list of providers that offer a specific service in a particular region, ordered by rating and credibility."""
    try:
        cache_key = f"services::{service_id}:providers:region:{region_id}:page:{page}:per_page:{per_page}"
        cached_data = await cache_service.get_json(cache_key)

        if cached_data:
            return BaseAPIResponse[PaginatedData[MinimalProviderResponse]](
                data=PaginatedData[MinimalProviderResponse](**cached_data),
                detail="Providers retrieved successfully from cache.",
                status_code=status.HTTP_200_OK,
            )

        statement, count_statement = (
            ServicesQueries.get_providers_for_service_in_region_query(
                service_id, region_id
            )
        )

        total_result = await user_repo.execute(count_statement)
        total = total_result.first() or 0

        statement = statement.offset((page - 1) * per_page).limit(per_page)

        results = await user_repo.execute(statement)
        users = results.all()

        items = []
        for u in users:
            fullname = None
            gender = None
            profile_picture_url = None
            if u.provider_profile:
                first_name = u.provider_profile.first_name or ""
                last_name = u.provider_profile.last_name or ""
                fullname = f"{first_name} {last_name}".strip() or None
                gender = u.provider_profile.gender
                profile_picture_url = u.provider_profile.selfie_url

            items.append(
                MinimalProviderResponse(
                    id=u.id,
                    email=u.email,
                    fullname=fullname,
                    average_ratings=u.average_ratings,
                    credibility_score=u.credibility_score,
                    gender=gender,
                    profile_picture_url=profile_picture_url,
                )
            )

        data = PaginatedData[MinimalProviderResponse](
            items=items, total=total, page=page, per_page=per_page
        )

        await cache_service.set_json(
            cache_key, data.model_dump(mode="json"), expire=300
        )

        return BaseAPIResponse[PaginatedData[MinimalProviderResponse]](
            data=data,
            detail="Providers retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving providers.",
        )
