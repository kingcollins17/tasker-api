from typing import Optional
from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlmodel import select, func, asc, desc, col
from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.repository import GetRepository, Repository
from app.core.models.services import ServiceCategory
from app.core.error_handler import AppErrorHandler
from app.features.services.schemas import CategoryResponse

router = APIRouter(prefix="/categories", tags=["Service Categories"])


@router.get("", response_model=BaseAPIResponse[PaginatedData[CategoryResponse]], status_code=status.HTTP_200_OK)
async def get_categories(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search by category name or description"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    sort_by: Optional[str] = Query("created_at", description="Field to sort by"),
    sort_desc: bool = Query(True, description="Sort descending"),
    category_repo: Repository[ServiceCategory] = Depends(GetRepository(ServiceCategory))
):
    """Retrieve a list of categories with pagination, filtering, searching and sorting."""
    try:
        statement = select(ServiceCategory)
        count_statement = select(func.count()).select_from(ServiceCategory)

        if search:
            search_filter = (col(ServiceCategory.name).ilike(f"%{search}%")) | (col(ServiceCategory.description).ilike(f"%{search}%"))
            statement = statement.where(search_filter)
            count_statement = count_statement.where(search_filter)
            
        if is_active is not None:
            statement = statement.where(ServiceCategory.is_active == is_active)
            count_statement = count_statement.where(ServiceCategory.is_active == is_active)

        # Count
        total_result = await category_repo.execute(count_statement)
        total = total_result.first() or 0

        # Order
        if sort_by and hasattr(ServiceCategory, sort_by):
            order_column = getattr(ServiceCategory, sort_by)
            statement = statement.order_by(desc(order_column) if sort_desc else asc(order_column))
            
        # Paginate
        statement = statement.offset((page - 1) * per_page).limit(per_page)
        
        results = await category_repo.execute(statement)
        categories = results.all()
        
        data = PaginatedData[CategoryResponse](
            items=[CategoryResponse.model_validate(c) for c in categories],
            total=total,
            page=page,
            per_page=per_page
        )
        
        return BaseAPIResponse[PaginatedData[CategoryResponse]](
            data=data,
            detail="Categories retrieved successfully.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving categories."
        )

@router.get("/{category_id}", response_model=BaseAPIResponse[CategoryResponse], status_code=status.HTTP_200_OK)
async def get_category(
    category_id: str,
    category_repo: Repository[ServiceCategory] = Depends(GetRepository(ServiceCategory))
):
    """Retrieve a single category by its ID."""
    try:
        category = await category_repo.get(category_id)
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category with ID {category_id} not found."
            )
            
        return BaseAPIResponse[CategoryResponse](
            data=CategoryResponse.model_validate(category),
            detail="Category retrieved successfully.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving the category."
        )
