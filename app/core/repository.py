from dataclasses import dataclass, field
from typing import Any, Dict, Generic, List, Optional, Type, TypeVar
from fastapi import Depends
from sqlmodel import select, SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy import desc

from app.core.database import get_session

T = TypeVar("T", bound=SQLModel)

@dataclass
class QueryOptions:
    filters: Dict[str, Any] = field(default_factory=dict)
    limit: Optional[int] = None
    offset: Optional[int] = None
    order_by: Optional[str] = None
    descending: bool = False

class Repository(Generic[T]):
    def __init__(self, model: Type[T], session: AsyncSession):
        self.model = model
        self.session = session

    async def get(self, id: Any) -> Optional[T]:
        """Fetch a single record by its primary key ID."""
        return await self.session.get(self.model, id)

    async def get_all(self, options: Optional[QueryOptions] = None) -> List[T]:
        """Fetch all records matching filters, order, and limit/offset bounds."""
        statement = select(self.model)
        
        if options:
            # Apply exact match filters dynamically
            for key, value in options.filters.items():
                if hasattr(self.model, key):
                    statement = statement.where(getattr(self.model, key) == value)
            
            # Apply ordering
            if options.order_by and hasattr(self.model, options.order_by):
                order_column = getattr(self.model, options.order_by)
                if options.descending:
                    statement = statement.order_by(desc(order_column))
                else:
                    statement = statement.order_by(order_column)
            
            # Apply offset & limit
            if options.offset is not None:
                statement = statement.offset(options.offset)
            if options.limit is not None:
                statement = statement.limit(options.limit)
                
        result = await self.session.exec(statement)
        return list(result.all())

    async def add(self, entity: T) -> T:
        """Add a new entity to the database session, commit, and refresh."""
        self.session.add(entity)
        await self.session.commit()
        await self.session.refresh(entity)
        return entity

    async def update(self, id: Any, data: Dict[str, Any]) -> Optional[T]:
        """Update fields of an existing entity in the database."""
        entity = await self.get(id)
        if not entity:
            return None
        
        for key, value in data.items():
            if hasattr(entity, key):
                setattr(entity, key, value)
                
        self.session.add(entity)
        await self.session.commit()
        await self.session.refresh(entity)
        return entity

    async def refresh(self, entity: T) -> None:
        """Refresh the attributes of the given entity from the database."""
        await self.session.refresh(entity)

    async def delete(self, id: Any) -> bool:
        """Delete an entity from the database by its ID."""
        entity = await self.get(id)
        if not entity:
            return False
        await self.session.delete(entity)
        await self.session.commit()
        return True

    async def execute(self, statement: Any) -> Any:
        """Execute a custom SQLModel/SQLAlchemy statement directly through the repository session."""
        return await self.session.exec(statement)



class GetRepository(Generic[T]):
    """FastAPI class dependency for obtaining a Repository instance for a specific model."""

    def __init__(self, model: Type[T]):
        self.model = model

    def __call__(self, session: AsyncSession = Depends(get_session)) -> Repository[T]:
        return Repository(self.model, session)

