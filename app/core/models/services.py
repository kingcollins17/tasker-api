from uuid import uuid4
from datetime import datetime, timezone
from app.core.utils.datetime_helper import utc_now
from typing import List, Optional, TYPE_CHECKING
from sqlmodel import Field, SQLModel, Relationship

if TYPE_CHECKING:
    from .users import ProviderProfile

class ProviderServiceLink(SQLModel, table=True):
    __tablename__ = "provider_services"  # type: ignore
    
    provider_id: str = Field(foreign_key="users.id", primary_key=True)
    service_id: str = Field(foreign_key="services.id", primary_key=True)

class ServiceCategory(SQLModel, table=True):
    __tablename__ = "categories"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str = Field(unique=True, index=True)  # e.g., "Home Improvement", "Automotive"
    description: Optional[str] = None
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    
    services: List["Service"] = Relationship(
        back_populates="category"
    )

class Service(SQLModel, table=True):
    __tablename__ = "services"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str = Field(unique=True, index=True)  # e.g., "plumber", "mechanic"
    take_rate: float = Field(default=0.10, description="Dynamic percentage take-rate specific to this service")
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    
    category_id: Optional[str] = Field(
        default=None,
        foreign_key="categories.id",
        ondelete="SET NULL",
        index=True
    )
    category: Optional[ServiceCategory] = Relationship(back_populates="services")
    
    providers: List["ProviderProfile"] = Relationship(
        back_populates="services",
        link_model=ProviderServiceLink,
        sa_relationship_kwargs={
            "primaryjoin": "Service.id == ProviderServiceLink.service_id",
            "secondaryjoin": "ProviderProfile.user_id == ProviderServiceLink.provider_id"
        }
    )
