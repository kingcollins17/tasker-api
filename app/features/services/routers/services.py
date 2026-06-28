from typing import Optional
from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlmodel import select, func, asc, desc, col
from sqlalchemy.orm import selectinload
from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.repository import GetRepository, Repository
from app.core.models.services import Service
from app.core.error_handler import AppErrorHandler
from app.features.services.schemas import ServiceResponse

router = APIRouter(prefix="/services", tags=["Services"])


@router.get("", response_model=BaseAPIResponse[PaginatedData[ServiceResponse]], status_code=status.HTTP_200_OK)
async def get_services(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search by service name"),
    category_id: Optional[str] = Query(None, description="Filter by category ID"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    sort_by: Optional[str] = Query("created_at", description="Field to sort by"),
    sort_desc: bool = Query(True, description="Sort descending"),
    service_repo: Repository[Service] = Depends(GetRepository(Service))
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
            statement = statement.order_by(desc(order_column) if sort_desc else asc(order_column))
            
        # Paginate
        statement = statement.offset((page - 1) * per_page).limit(per_page)
        
        results = await service_repo.execute(statement)
        services = results.all()
        
        data = PaginatedData[ServiceResponse](
            items=[ServiceResponse.model_validate(s) for s in services],
            total=total,
            page=page,
            per_page=per_page
        )
        
        return BaseAPIResponse[PaginatedData[ServiceResponse]](
            data=data,
            detail="Services retrieved successfully.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving services."
        )

@router.get("/{service_id}", response_model=BaseAPIResponse[ServiceResponse], status_code=status.HTTP_200_OK)
async def get_service(
    service_id: str,
    service_repo: Repository[Service] = Depends(GetRepository(Service))
):
    """Retrieve a single service by its ID."""
    try:
        statement = select(Service).where(Service.id == service_id).options(selectinload(Service.category))  # type: ignore
        results = await service_repo.execute(statement)
        service = results.first()
        
        if not service:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Service with ID {service_id} not found."
            )
            
        return BaseAPIResponse[ServiceResponse](
            data=ServiceResponse.model_validate(service),
            detail="Service retrieved successfully.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving the service."
        )
