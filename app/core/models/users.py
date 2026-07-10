import enum
from datetime import datetime, timezone
from uuid import uuid4
from app.core.utils.datetime_helper import utc_now
from typing import List, Optional, Any
from sqlmodel import Field, SQLModel, Relationship
from sqlalchemy import Column, JSON
from .spatial import PointType
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

class PaymentProvider(str, enum.Enum):
    PAYSTACK = "paystack"
    MONNIFY = "monnify"
    FLUTTERWAVE = "flutterwave"

class User(SQLModel, table=True):
    __tablename__ = "users"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    phone_number: Optional[str] = Field(unique=True, index=True, default=None)
    email: str = Field(unique=True, index=True)
    hashed_password: str = Field(nullable=False)
    type: UserType
    is_active: bool = Field(default=False)
    email_verified: bool = Field(default=False)
    phone_verified: bool = Field(default=False)
    region_id: Optional[str] = Field(default=None, foreign_key="regions.id", nullable=True, index=True)
    credibility_score: float = Field(default=25.0)
    average_ratings: float = Field(default=0.0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    
    provider_profile: Optional["ProviderProfile"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"uselist": False, "lazy": "joined"}
    )
    customer_profile: Optional["CustomerProfile"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"uselist": False, "lazy": "joined"}
    )
    payment_accounts: List["PaymentAccount"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    devices: List["UserDevice"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"}
    )
    location: Optional["UserLocation"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"uselist": False, "cascade": "all, delete-orphan", "lazy": "joined"}
    )

class ProviderProfile(SQLModel, table=True):
    __tablename__ = "provider_profiles"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", unique=True)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    id_type: Optional[str] = None  # 'NIN', 'BVN'
    id_number: Optional[str] = None
    id_doc_url: Optional[str] = None
    selfie_url: Optional[str] = None
    gender: Optional[str] = None
    
    status: KYCStatus = Field(default=KYCStatus.PENDING_SUBMISSION)
    provider_reference: Optional[str] = Field(default=None, index=True)
    liveness_score: Optional[float] = None
    rejection_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    verified_at: Optional[datetime] = None
    user: User = Relationship(back_populates="provider_profile")

    services: List[Service] = Relationship(
        back_populates="providers",
        link_model=ProviderServiceLink,
        sa_relationship_kwargs={
            "primaryjoin": "ProviderProfile.user_id == ProviderServiceLink.provider_id",
            "secondaryjoin": "Service.id == ProviderServiceLink.service_id",
            "lazy": "selectin"
        }
    )

class PaymentAccount(SQLModel, table=True):
    __tablename__ = "payment_accounts"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(
        foreign_key="users.id",
        index=True
    )
    provider: PaymentProvider
    external_account_id: Optional[str]=None
    account_name: Optional[str] = None
    is_active: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=utc_now
    )
    updated_at: datetime = Field(
        default_factory=utc_now
    )
    account_metadata: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON)
    )
    
    user: User = Relationship(back_populates="payment_accounts")


class CustomerProfile(SQLModel, table=True):
    __tablename__ = "customer_profiles"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", unique=True)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    
    user: User = Relationship(back_populates="customer_profile")


class UserLocation(SQLModel, table=True):
    __tablename__ = "user_locations"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", unique=True, index=True)
    region_id: Optional[str] = Field(default=None, foreign_key="regions.id", nullable=True, index=True)
    last_known_location: Optional[Any] = Field(
        default=None,
        sa_column=Column(PointType, nullable=True)
    )
    address_line: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    
    user: User = Relationship(back_populates="location")


class UserDevice(SQLModel, table=True):
    __tablename__ = "user_devices"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    platform: str = Field(description="platform-ios|android")
    messaging_token: str = Field(unique=True, index=True)
    is_active: bool = Field(default=True)
    last_login_at: Optional[datetime] = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    
    user: User = Relationship(back_populates="devices")



