from uuid import uuid4
from typing import List, TYPE_CHECKING
from sqlmodel import Field, SQLModel, Relationship

if TYPE_CHECKING:
    from .users import ProviderProfile

class ProviderServiceLink(SQLModel, table=True):
    __tablename__ = "provider_services"  # type: ignore
    
    provider_id: str = Field(foreign_key="provider_profiles.id", primary_key=True)
    service_id: str = Field(foreign_key="services.id", primary_key=True)

class Service(SQLModel, table=True):
    __tablename__ = "services"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str = Field(unique=True, index=True)  # e.g., "plumber", "mechanic"
    take_rate: float = Field(default=0.10, description="Dynamic percentage take-rate specific to this service")
    is_active: bool = Field(default=True)
    
    providers: List["ProviderProfile"] = Relationship(
        back_populates="services", link_model=ProviderServiceLink
    )
