from datetime import datetime
from typing import List, Optional
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from app.core.models.services import Service, ServiceCategory
from app.core.repository import GetRepository, Repository
from app.core.utils.datetime_helper import lagos_now
from app.features.services.schemas import CreateServiceRequest, UpdateServiceRequest


class ServiceService:
    def __init__(
        self,
        service_repo: Repository[Service],
        category_repo: Repository[ServiceCategory],
    ):
        self.service_repo = service_repo
        self.category_repo = category_repo

    async def create_service(self, schema: CreateServiceRequest) -> Service:
        """Create a new service."""
        statement = select(Service).where(col(Service.name).ilike(schema.name))
        existing_result = await self.service_repo.execute(statement)
        existing = existing_result.first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Service with name '{schema.name}' already exists."
            )

        if schema.category_id:
            category = await self.category_repo.get(schema.category_id)
            if not category:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Category with ID '{schema.category_id}' not found."
                )

        service = Service(
            name=schema.name,
            category_id=schema.category_id,
            image_url=schema.image_url,
            base_price=schema.base_price,
            default_duration_min=schema.default_duration_min,
            per_km_rate=schema.per_km_rate,
            per_minute_rate=schema.per_minute_rate,
            take_rate=schema.take_rate if schema.take_rate is not None else 0.15,
            min_tier_required=schema.min_tier_required if schema.min_tier_required is not None else 4,
            is_high_risk=schema.is_high_risk if schema.is_high_risk is not None else False,
            is_active=schema.is_active if schema.is_active is not None else True,
        )
        created_service = await self.service_repo.add(service)

        fetch_stmt = select(Service).where(Service.id == created_service.id).options(selectinload(Service.category))  # type: ignore
        res = await self.service_repo.execute(fetch_stmt)
        return res.first() or created_service

    async def update_service(self, service_id: str, schema: UpdateServiceRequest) -> Service:
        """Update an existing service."""
        service = await self.service_repo.get(service_id)
        if not service:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Service with ID '{service_id}' not found."
            )

        if schema.name is not None and schema.name.lower() != service.name.lower():
            statement = select(Service).where(col(Service.name).ilike(schema.name))
            existing_result = await self.service_repo.execute(statement)
            existing = existing_result.first()
            if existing and existing.id != service_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Service with name '{schema.name}' already exists."
                )

        if schema.category_id is not None and schema.category_id != service.category_id:
            category = await self.category_repo.get(schema.category_id)
            if not category:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Category with ID '{schema.category_id}' not found."
                )

        update_data = schema.model_dump(exclude_unset=True)
        update_data["updated_at"] = lagos_now()

        updated_service = await self.service_repo.update(service_id, update_data)
        if not updated_service:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Service with ID '{service_id}' not found."
            )

        fetch_stmt = select(Service).where(Service.id == service_id).options(selectinload(Service.category))  # type: ignore
        res = await self.service_repo.execute(fetch_stmt)
        return res.first() or updated_service

    async def delete_service(self, service_id: str) -> bool:
        """Delete a service by ID."""
        service = await self.service_repo.get(service_id)
        if not service:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Service with ID '{service_id}' not found."
            )
        return await self.service_repo.delete(service_id)


def get_service_service(
    service_repo: Repository[Service] = Depends(GetRepository(Service)),
    category_repo: Repository[ServiceCategory] = Depends(GetRepository(ServiceCategory)),
) -> ServiceService:
    return ServiceService(
        service_repo=service_repo,
        category_repo=category_repo,
    )
