import enum
from datetime import datetime, timezone
from uuid import uuid4
from typing import List, Optional, Any
from sqlmodel import Field, SQLModel, Relationship
from sqlalchemy import Column, JSON
from sqlalchemy.types import TypeDecorator, String
from geoalchemy2 import Geometry

class PointType(TypeDecorator):
    impl = Geometry
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect is None or dialect.name == "sqlite":
            return String()
        else:
            return Geometry(geometry_type="POINT", srid=4326, spatial_index=True)

from .services import ProviderServiceLink, Service

class UserType(str, enum.Enum):
    CUSTOMER = "customer"
    PROVIDER = "provider"

class KYCStatus(str, enum.Enum):
    PENDING_SUBMISSION = "pending_submission"
    SUBMITTED = "submitted"
    PENDING_ADMIN_REVIEW = "pending_admin_review"
    VERIFIED = "verified"
    FAILED = "failed"

class AdminRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    COMPLIANCE_OFFICER = "compliance_officer"
    SUPPORT_AGENT = "support_agent"

class PaymentProvider(str, enum.Enum):
    PAYSTACK = "paystack"
    MONNIFY = "monnify"
    FLUTTERWAVE = "flutterwave"

class User(SQLModel, table=True):
    __tablename__ = "users"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    phone_number: Optional[str] = Field(unique=True, index=True, default=None)
    email: str = Field(unique=True, index=True)
    type: UserType
    is_active: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    provider_profile: Optional["ProviderProfile"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"uselist": False}
    )
    customer_profile: Optional["CustomerProfile"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"uselist": False}
    )

class ProviderProfile(SQLModel, table=True):
    __tablename__ = "provider_profiles"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", unique=True)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    id_type: Optional[str] = None  # 'NIN', 'BVN'
    id_number: Optional[str] = None
    selfie_s3_url: Optional[str] = None
    
    # Portfolio Track
    resume_s3_url: Optional[str] = Field(default=None)
    resume_uploaded_at: Optional[datetime] = Field(default=None)
    
    status: KYCStatus = Field(default=KYCStatus.PENDING_SUBMISSION)
    provider_reference: Optional[str] = Field(default=None, index=True)
    liveness_score: Optional[float] = None
    rejection_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    verified_at: Optional[datetime] = None
    last_known_location: Optional[Any] = Field(
        default=None,
        sa_column=Column(PointType, nullable=True)
    )

    user: User = Relationship(back_populates="provider_profile")
    services: List[Service] = Relationship(
        back_populates="providers", link_model=ProviderServiceLink
    )
    payment_accounts: List["ProviderPaymentAccount"] = Relationship(
        back_populates="provider_profile",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )

class ProviderPaymentAccount(SQLModel, table=True):
    __tablename__ = "provider_payment_accounts"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    provider_id: str = Field(
        foreign_key="provider_profiles.id",
        index=True
    )
    provider: PaymentProvider
    external_account_id: Optional[str]=None
    account_name: Optional[str] = None
    is_active: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    account_metadata: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON)
    )
    
    provider_profile: ProviderProfile = Relationship(back_populates="payment_accounts")


class CustomerProfile(SQLModel, table=True):
    __tablename__ = "customer_profiles"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", unique=True)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_known_location: Optional[Any] = Field(
        default=None,
        sa_column=Column(PointType, nullable=True)
    )
    
    user: User = Relationship(back_populates="customer_profile")

class AdminUser(SQLModel, table=True):
    __tablename__ = "admin_users"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    full_name: Optional[str]=None
    role: AdminRole
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
