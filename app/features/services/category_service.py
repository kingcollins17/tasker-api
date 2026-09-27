from datetime import datetime
from typing import List, Optional
from fastapi import Depends, HTTPException, status
from sqlmodel import col, select

from app.core.models.services import ServiceCategory
from app.core.repository import GetRepository, Repository
from app.core.utils.datetime_helper import lagos_now
from app.features.services.schemas import CreateCategoryRequest, UpdateCategoryRequest


class CategoryService:
    def __init__(self, category_repo: Repository[ServiceCategory]):
        self.category_repo = category_repo

    async def create_category(self, schema: CreateCategoryRequest) -> ServiceCategory:
        """Create a new service category."""
        statement = select(ServiceCategory).where(col(ServiceCategory.name).ilike(schema.name))
        existing_result = await self.category_repo.execute(statement)
        existing = existing_result.first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Category with name '{schema.name}' already exists."
            )

        category = ServiceCategory(
            name=schema.name,
            description=schema.description,
            image_url=schema.image_url,
            default_base_price=schema.default_base_price,
            default_duration_min=schema.default_duration_min,
            per_km_rate=schema.per_km_rate,
            per_minute_rate=schema.per_minute_rate,
            is_active=schema.is_active if schema.is_active is not None else True,
        )
        return await self.category_repo.add(category)

    async def update_category(self, category_id: str, schema: UpdateCategoryRequest) -> ServiceCategory:
        """Update an existing service category."""
        category = await self.category_repo.get(category_id)
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category with ID '{category_id}' not found."
            )

        if schema.name is not None and schema.name.lower() != category.name.lower():
            statement = select(ServiceCategory).where(col(ServiceCategory.name).ilike(schema.name))
            existing_result = await self.category_repo.execute(statement)
            existing = existing_result.first()
            if existing and existing.id != category_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Category with name '{schema.name}' already exists."
                )

        update_data = schema.model_dump(exclude_unset=True)
        update_data["updated_at"] = lagos_now()

        updated_category = await self.category_repo.update(category_id, update_data)
        if not updated_category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category with ID '{category_id}' not found."
            )
        return updated_category

    async def delete_category(self, category_id: str) -> bool:
        """Delete a service category by ID."""
        category = await self.category_repo.get(category_id)
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category with ID '{category_id}' not found."
            )
        return await self.category_repo.delete(category_id)


def get_category_service(
    category_repo: Repository[ServiceCategory] = Depends(GetRepository(ServiceCategory)),
) -> CategoryService:
    return CategoryService(category_repo=category_repo)
