import enum
from datetime import datetime, timezone
from uuid import uuid4
from app.core.utils.datetime_helper import lagos_now
from typing import List, Optional, Any
from sqlmodel import Field, SQLModel, Relationship
from sqlalchemy import Column, JSON, UniqueConstraint, Time
from .spatial import PointType
from datetime import time
from .services import ProviderServiceLink, Service

class UserType(str, enum.Enum):
    """Discriminates user role types within the platform."""
    CUSTOMER = "customer"
    PROVIDER = "provider"

class KYCStatus(str, enum.Enum):
    """Tracks Know-Your-Customer verification workflow states for service providers."""
    PENDING_SUBMISSION = "pending_submission"
    SUBMITTED = "submitted"
    PENDING_ADMIN_REVIEW = "pending_admin_review"
    VERIFIED = "verified"
    FAILED = "failed"

class PaymentProvider(str, enum.Enum):
    """Supported payment gateway integration partners."""
    PAYSTACK = "paystack"
    MONNIFY = "monnify"
    FLUTTERWAVE = "flutterwave"

class DutyStatus(str, enum.Enum):
    """Real-time availability and dispatch activity state for service providers."""
    OFFLINE = "offline"
    ONLINE_AVAILABLE = "online_available"
    ON_DISPATCH = "on_dispatch"
    ON_TASK = "on_task"

class VerificationStatus(str, enum.Enum):
    """Status for verification checks."""
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    UNDER_REVIEW = "under_review"

class OnboardingStep(str, enum.Enum):
    """Current step in the provider onboarding vetting process."""
    KYC = "kyc"
    TRADE_QUIZ = "trade_quiz"
    TOOL_PROOF = "tool_proof"
    GUARANTOR = "guarantor"
    COMPLETED = "completed"

class MediaType(str, enum.Enum):
    """Type of portfolio media uploaded by a provider."""
    TOOL_PHOTO = "tool_photo"
    PAST_WORK_VIDEO = "past_work_video"
    WORKSPACE = "workspace"

class DayOfWeek(int, enum.Enum):
    SUNDAY = 1
    MONDAY = 2
    TUESDAY = 3
    WEDNESDAY = 4
    THURSDAY = 5
    FRIDAY = 6
    SATURDAY = 7

class User(SQLModel, table=True):
    """Core user identity table containing login credentials, verification flags, and role assignments."""
    __tablename__ = "users"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique primary identifier for the user")
    phone_number: Optional[str] = Field(unique=True, index=True, default=None, description="E.164 format unique phone number")
    email: str = Field(unique=True, index=True, description="Primary unique email address")
    hashed_password: str = Field(nullable=False, description="Bcrypt password hash string")
    type: UserType = Field(description="Role type of the user (customer or provider)")
    is_active: bool = Field(default=False, description="Whether the user account is active and enabled")
    email_verified: bool = Field(default=False, description="Whether the user's email address has been verified")
    phone_verified: bool = Field(default=False, description="Whether the user's phone number has been verified")
    region_id: Optional[str] = Field(default=None, foreign_key="regions.id", nullable=True, index=True, description="Default geographical region assignment")
    credibility_score: float = Field(default=25.0, description="Platform credibility score metric based on history")
    average_ratings: float = Field(default=0.0, description="Aggregated average rating score across completed tasks")
    total_ratings: int = Field(default=0, description="Total number of ratings received from completed tasks")
    created_at: datetime = Field(default_factory=lagos_now, description="Timestamp when the user registered")
    updated_at: datetime = Field(default_factory=lagos_now, description="Timestamp when user details were last updated")
    
    provider_profile: Optional["ProviderProfile"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"uselist": False, "lazy": "joined"}
    )
    customer_profile: Optional["CustomerProfile"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"uselist": False, "lazy": "joined"}
    )
    payment_account: Optional["PaymentAccount"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"uselist": False, "cascade": "all, delete-orphan", "lazy": "joined"}
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
    """Detailed profile data, KYC verification state, presence, and performance metrics for task service providers."""
    __tablename__ = "provider_profiles"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique primary identifier for the provider profile")
    user_id: str = Field(foreign_key="users.id", unique=True, ondelete="CASCADE", description="Foreign key reference to the core user account")
    first_name: Optional[str] = Field(default=None, description="Legal first name of provider")
    last_name: Optional[str] = Field(default=None, description="Legal last name of provider")
    id_type: Optional[str] = Field(default=None, description="Type of government identification document (e.g., NIN, BVN)")
    id_number: Optional[str] = Field(default=None, description="Unique identification document number")
    id_doc_url: Optional[str] = Field(default=None, description="Cloud storage URL for uploaded ID document")
    selfie_url: Optional[str] = Field(default=None, description="Cloud storage URL for uploaded verification selfie")
    gender: Optional[str] = Field(default=None, description="Gender of the provider")
    
    current_tier: int = Field(default=1, le=5, ge=1, description="Provider trade tier level (1 to 5)")
    current_onboarding_step: OnboardingStep = Field(default=OnboardingStep.KYC, description="Current progress step in the vetting pipeline")

    status: KYCStatus = Field(default=KYCStatus.PENDING_SUBMISSION, description="Current status of KYC document verification")
    provider_reference: Optional[str] = Field(default=None, index=True, description="Third-party identity verification provider reference ID")
    liveness_score: Optional[float] = Field(default=None, description="Facial liveness confidence score from verification check")
    rejection_reason: Optional[str] = Field(default=None, description="Reason stated if KYC verification was rejected")
    is_online: Optional[bool] = Field(default=False, index=True, nullable=True, description="Real-time online presence toggle. Updated via mobile app toggle API when provider switches online/offline state.")
    duty_status: Optional[DutyStatus] = Field(default=DutyStatus.OFFLINE, index=True, nullable=True, description="Current dispatch activity state (OFFLINE, ONLINE_AVAILABLE, ON_DISPATCH, ON_TASK). Updated by ProviderLocationService, cascading dispatcher, and task lifecycle events.")
    last_heartbeat_at: Optional[datetime] = Field(default=None, nullable=True, description="Timestamp of the most recent location/presence ping. Updated continuously via ProviderLocationService.update_provider_location().")
    acceptance_rate_30d: Optional[float] = Field(default=100.0, nullable=True, description="Rolling 30-day percentage of accepted dispatch pings (accepted / total dispatches * 100). Updated asynchronously by background Celery metrics task or after dispatch attempt completion.")
    completion_rate_30d: Optional[float] = Field(default=100.0, nullable=True, description="Rolling 30-day percentage of successfully completed assigned tasks (completed / assigned * 100). Updated asynchronously by background Celery metrics task or task completion events.")
    total_tasks_completed: Optional[int] = Field(default=0, nullable=True, description="Lifetime total count of successfully completed tasks. Incremented by 1 when a task transitions to COMPLETED status.")
    consecutive_declines: Optional[int] = Field(default=0, nullable=True, description="Count of consecutive dispatch ping declines or timeouts. Incremented on declined/expired pings, reset to 0 on acceptance. Used to auto-pause inactive providers.")
    cancellation_count: int = Field(default=0, description="Number of times the provider has cancelled accepted tasks")

    
    created_at: datetime = Field(default_factory=lagos_now, description="Record creation timestamp")
    updated_at: datetime = Field(default_factory=lagos_now, description="Record last updated timestamp")
    verified_at: Optional[datetime] = Field(default=None, description="Timestamp when provider was fully KYC verified")
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
    """Payment processor sub-account or wallet details for managing payouts and billing."""
    __tablename__ = "payment_accounts"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique payment account ID")
    user_id: str = Field(foreign_key="users.id", unique=True, index=True, ondelete="CASCADE", description="Foreign key reference to user")
    provider: PaymentProvider = Field(description="Payment provider gateway engine")
    external_account_id: Optional[str] = Field(default=None, description="External payment provider recipient/sub-account ID")
    account_name: Optional[str] = Field(default=None, description="Bank account or recipient display name")
    is_active: bool = Field(default=True, description="Whether this payment account is active for transactions")
    created_at: datetime = Field(default_factory=lagos_now, description="Record creation timestamp")
    updated_at: datetime = Field(default_factory=lagos_now, description="Record update timestamp")
    account_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON), description="Provider-specific JSON metadata payload")
    
    user: User = Relationship(back_populates="payment_account")

class CustomerProfile(SQLModel, table=True):
    """Profile data for customer accounts requesting service tasks."""
    __tablename__ = "customer_profiles"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique customer profile ID")
    user_id: str = Field(foreign_key="users.id", unique=True, ondelete="CASCADE", description="Foreign key reference to core user account")
    first_name: Optional[str] = Field(default=None, description="Customer first name")
    last_name: Optional[str] = Field(default=None, description="Customer last name")
    created_at: datetime = Field(default_factory=lagos_now, description="Record creation timestamp")
    updated_at: datetime = Field(default_factory=lagos_now, description="Record update timestamp")
    
    user: User = Relationship(back_populates="customer_profile")

class UserLocation(SQLModel, table=True):
    """Tracks last known location coordinates and spatial PostGIS point for users."""
    __tablename__ = "user_locations"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique location entry ID")
    user_id: str = Field(foreign_key="users.id", unique=True, index=True, ondelete="CASCADE", description="Foreign key reference to user")
    region_id: Optional[str] = Field(default=None, foreign_key="regions.id", nullable=True, index=True, description="Assigned geographical region ID")
    timezone: str = Field(default="UTC", description="IANA Timezone string (e.g., Africa/Lagos)")
    last_known_location: Optional[Any] = Field(default=None, sa_column=Column(PointType, nullable=True), description="PostGIS Point spatial geography column")
    latitude: Optional[float] = Field(default=None, description="WGS84 Latitude coordinate")
    longitude: Optional[float] = Field(default=None, description="WGS84 Longitude coordinate")
    address_line: Optional[str] = Field(default=None, description="Formatted street address string")
    created_at: datetime = Field(default_factory=lagos_now, description="Record creation timestamp")
    updated_at: datetime = Field(default_factory=lagos_now, description="Record update timestamp")
    
    user: User = Relationship(back_populates="location")

class UserDevice(SQLModel, table=True):
    """Mobile/web client device push notification tokens registered to user accounts."""
    __tablename__ = "user_devices"  # type: ignore
    __table_args__ = (UniqueConstraint("user_id", "platform", name="uq_user_device_platform"),)
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique device entry ID")
    user_id: str = Field(foreign_key="users.id", index=True, ondelete="CASCADE", description="Foreign key reference to user")
    platform: str = Field(description="Device platform (e.g. 'ios' or 'android')")
    messaging_token: str = Field(unique=True, index=True, description="Firebase FCM or APNS push notification token")
    is_active: bool = Field(default=True, description="Whether push notification delivery to this device is enabled")
    last_login_at: Optional[datetime] = Field(default_factory=lagos_now, description="Timestamp of last device session login")
    created_at: datetime = Field(default_factory=lagos_now, description="Record creation timestamp")
    updated_at: datetime = Field(default_factory=lagos_now, description="Record update timestamp")
    
    user: User = Relationship(back_populates="devices")

class ProviderAvailability(SQLModel, table=True):
    """Recurring weekly schedule for provider availability."""
    __tablename__ = "provider_availabilities"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique availability block ID")
    provider_id: str = Field(foreign_key="users.id", index=True, ondelete="CASCADE", description="Foreign key reference to provider user account")
    day_of_week: DayOfWeek = Field(description="1=Sunday, 7=Saturday")
    
    start_time: time = Field(sa_column=Column(Time, nullable=False), description="Start time (e.g., 07:00:00)")
    end_time: time = Field(sa_column=Column(Time, nullable=False), description="End time (e.g., 18:00:00)")
    
    provider: User = Relationship()
