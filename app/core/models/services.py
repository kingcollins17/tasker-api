import enum
from uuid import uuid4
from datetime import datetime, timezone
from app.core.utils.datetime_helper import utc_now
from typing import List, Optional, TYPE_CHECKING
from sqlmodel import Field, SQLModel, Relationship

if TYPE_CHECKING:
    from .users import ProviderProfile

class PricingRuleType(str, enum.Enum):
    """Categorizes dynamic pricing rule calculation types."""
    BASE_RATE = "base_rate"
    PER_KM = "per_km"
    PER_MINUTE = "per_minute"
    URGENCY_FEE = "urgency_fee"
    COMPLEXITY_FLAT = "complexity_flat"
    SURGE_MULTIPLIER = "surge_multiplier"

class ProviderServiceLink(SQLModel, table=True):
    """Many-to-many junction table mapping provider profiles to offered services."""
    __tablename__ = "provider_services"  # type: ignore
    
    provider_id: str = Field(foreign_key="users.id", primary_key=True, ondelete="CASCADE", description="Foreign key reference to provider user ID")
    service_id: str = Field(foreign_key="services.id", primary_key=True, description="Foreign key reference to service ID")

class ServiceCategory(SQLModel, table=True):
    """Top-level category grouping related services with default base pricing rates."""
    __tablename__ = "categories"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique primary identifier for category")
    name: str = Field(unique=True, index=True, description="Category name (e.g. 'Home Improvement', 'Cleaning')")
    description: Optional[str] = Field(default=None, description="Detailed category summary description")
    image_url: Optional[str] = Field(default=None, description="Public image thumbnail URL for category icon")
    default_base_price: Optional[float] = Field(default=0.0, nullable=True, description="Default base price for tasks in this category")
    default_duration_min: Optional[int] = Field(default=60, nullable=True, description="Default expected duration in minutes")
    per_km_rate: Optional[float] = Field(default=150.0, nullable=True, description="Default per-kilometer distance fee rate")
    per_minute_rate: Optional[float] = Field(default=20.0, nullable=True, description="Default per-minute labor fee rate")
    is_active: bool = Field(default=True, description="Whether this category is active and visible to customers")
    created_at: datetime = Field(default_factory=utc_now, description="Record creation timestamp")
    updated_at: datetime = Field(default_factory=utc_now, description="Record update timestamp")
    
    services: List["Service"] = Relationship(
        back_populates="category"
    )

class Service(SQLModel, table=True):
    """Specific task service definition offering specific skills and take rates."""
    __tablename__ = "services"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique primary identifier for service")
    name: str = Field(unique=True, index=True, description="Specific service title (e.g. 'Plumber', 'Mechanic')")
    image_url: Optional[str] = Field(default=None, description="Public image thumbnail URL for service")
    base_price: Optional[float] = Field(default=0.0, nullable=True, description="Base price specific to this service")
    default_duration_min: Optional[int] = Field(default=60, nullable=True, description="Default expected duration specific to this service")
    per_km_rate: Optional[float] = Field(default=150.0, nullable=True, description="Per-kilometer distance rate override")
    per_minute_rate: Optional[float] = Field(default=20.0, nullable=True, description="Per-minute labor rate override")
    take_rate: float = Field(default=0.15, description="Dynamic percentage platform commission take-rate")
    is_active: bool = Field(default=True, description="Whether this service is active and bookable")
    created_at: datetime = Field(default_factory=utc_now, description="Record creation timestamp")
    updated_at: datetime = Field(default_factory=utc_now, description="Record update timestamp")
    
    category_id: Optional[str] = Field(
        default=None,
        foreign_key="categories.id",
        ondelete="SET NULL",
        index=True,
        description="Foreign key reference to parent category"
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

class PricingRule(SQLModel, table=True):
    """Configurable dynamic pricing rules, surcharges, and multipliers per category or region."""
    __tablename__ = "pricing_rules"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique pricing rule ID")
    category_id: Optional[str] = Field(
        default=None, foreign_key="categories.id", index=True, ondelete="CASCADE", nullable=True, description="Optional foreign key targeting a category"
    )
    region_id: Optional[str] = Field(
        default=None, foreign_key="regions.id", index=True, ondelete="CASCADE", nullable=True, description="Optional foreign key targeting a specific region"
    )
    rule_type: Optional[PricingRuleType] = Field(default=None, index=True, nullable=True, description="Type of pricing modifier rule")
    value: Optional[float] = Field(default=0.0, nullable=True, description="Fixed numerical value or fee additive")
    multiplier: Optional[float] = Field(default=1.0, nullable=True, description="Scaling factor multiplier (e.g. 1.25 for surge)")
    is_active: Optional[bool] = Field(default=True, nullable=True, description="Whether this pricing rule is currently active")
    created_at: datetime = Field(default_factory=utc_now, description="Record creation timestamp")
    updated_at: datetime = Field(default_factory=utc_now, description="Record update timestamp")
