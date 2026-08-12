import re
from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, Literal, List
from datetime import datetime
from app.core.models.users import UserType, KYCStatus, DutyStatus, DayOfWeek
from app.core.schemas.users import PaymentAccountResponse, UserLocationResponse
from app.core.utils.phone_helper import format_nigerian_phone
from datetime import time


class UserRegister(BaseModel):
    email: str = Field(..., description="Email address of the user")
    phone_number: Optional[str] = Field(
        None, description="Optional phone number of the user")
    password: str = Field(..., min_length=8,
                          description="Password must be at least 8 characters long")
    type: UserType = Field(...,
                           description="Type of user registering (customer or provider)")
    first_name: Optional[str] = Field(
        None, description="First name for profile initialization")
    last_name: Optional[str] = Field(
        None, description="Last name for profile initialization")
    gender: Optional[str] = Field(
        None, description="Optional gender of the provider")
    region_id: Optional[str] = Field(
        None, description="Optional region ID for the user")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not re.match(r"[^@]+@[^@]+\.[^@]+", v):
            raise ValueError("Invalid email format")
        return v

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return format_nigerian_phone(v)
        return v


class CustomerProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    address_line: Optional[str] = None


class ServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    name: Optional[str] = None
    take_rate: Optional[float] = None
    is_active: Optional[bool] = None


class PublicProviderProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str]=None
    user_id: Optional[str]=None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    gender: Optional[str] = None
    selfie_url: Optional[str] = None
    is_online: Optional[bool] = None
    duty_status: Optional[DutyStatus] = None
    last_heartbeat_at: Optional[datetime] = None
    total_tasks_completed: Optional[int] = 0


class PublicUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    type: Optional[UserType] = None
    is_active: Optional[bool] = None
    email_verified: Optional[bool] = None
    phone_verified: Optional[bool] = None
    credibility_score: Optional[float] = None
    average_ratings: Optional[float] = None
    created_at: Optional[datetime] = None
    region_id: Optional[str] = None
    location: Optional[UserLocationResponse] = None
    services: List[ServiceResponse] = []
    availability: List["ProviderAvailabilityResponse"] = []
    profile: Optional[PublicProviderProfileResponse] = None



class ProviderProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str]=None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    id_type: Optional[str] = None
    id_number: Optional[str] = None
    id_doc_url: Optional[str] = None
    selfie_url: Optional[str] = None
    gender: Optional[str] = None
    status: KYCStatus
    provider_reference: Optional[str] = None
    liveness_score: Optional[float] = None
    rejection_reason: Optional[str] = None
    verified_at: Optional[datetime] = None
    address_line: Optional[str] = None
    is_online: Optional[bool] = None
    duty_status: Optional[DutyStatus] = None
    last_heartbeat_at: Optional[datetime] = None
    total_tasks_completed: Optional[int] = 0
    services: List[ServiceResponse] = []

class UserDeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    user_id: Optional[str] = None
    platform: Optional[str] = None
    messaging_token: Optional[str] = None
    is_active: Optional[bool] = None
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    phone_number: Optional[str] = None
    type: UserType
    is_active: bool
    email_verified: bool
    phone_verified: bool
    credibility_score: Optional[float] = 25.0
    average_ratings: Optional[float] = 0.0
    created_at: datetime
    updated_at: datetime
    region_id: Optional[str] = None
    customer_profile: Optional[CustomerProfileResponse] = None
    provider_profile: Optional[ProviderProfileResponse] = None
    devices: Optional[List[UserDeviceResponse]] = []
    location: Optional[UserLocationResponse] = None
    payment_account: Optional[PaymentAccountResponse] = None



class RequestEmailOTP(BaseModel):
    email: str = Field(...,
                       description="Email address of the user requesting OTP")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not re.match(r"[^@]+@[^@]+\.[^@]+", v):
            raise ValueError("Invalid email format")
        return v


class VerifyEmailOTP(BaseModel):
    email: str = Field(...,
                       description="Email address of the user verifying OTP")
    code: str = Field(..., min_length=6, max_length=6,
                      description="6-digit OTP code")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not re.match(r"[^@]+@[^@]+\.[^@]+", v):
            raise ValueError("Invalid email format")
        return v


class RequestPhoneOTP(BaseModel):
    phone_number: str = Field(...,
                              description="Phone number of the user requesting OTP")

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return format_nigerian_phone(v)


class VerifyPhoneOTP(BaseModel):
    phone_number: str = Field(...,
                              description="Phone number of the user verifying OTP")
    code: str = Field(..., min_length=6, max_length=6,
                      description="6-digit OTP code")

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return format_nigerian_phone(v)


class UserLogin(BaseModel):
    email: str = Field(..., description="Email address of the user")
    password: str = Field(..., description="Password of the user")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not re.match(r"[^@]+@[^@]+\.[^@]+", v):
            raise ValueError("Invalid email format")
        return v


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class ProviderProfileUpdate(BaseModel):
    first_name: Optional[str] = Field(None, description="Updated first name")
    last_name: Optional[str] = Field(None, description="Updated last name")
    gender: Optional[str] = Field(None, description="Updated gender")
    phone_number: Optional[str] = Field(
        None, description="Updated phone number")

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return format_nigerian_phone(v)
        return v


class VerifyOTP(BaseModel):
    target: str = Field(...,
                        description="The email address or phone number the OTP was sent to")
    channel: str = Field(...,
                         description="The delivery channel ('email' or 'sms')")
    code: str = Field(..., min_length=6, max_length=6,
                      description="6-digit OTP code")

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, v: str) -> str:
        v = v.lower()
        if v not in ("email", "sms"):
            raise ValueError("Channel must be 'email' or 'sms'")
        return v


class CustomerProfileUpdate(BaseModel):
    first_name: Optional[str] = Field(None, description="Updated first name")
    last_name: Optional[str] = Field(None, description="Updated last name")
    phone_number: Optional[str] = Field(
        None, description="Updated phone number")

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return format_nigerian_phone(v)
        return v


class UpdateLocation(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0,
                            description="Latitude coordinate")
    longitude: float = Field(..., ge=-180.0, le=180.0,
                             description="Longitude coordinate")
    address_line: Optional[str] = Field(
        None, description="Optional address line description of the location")
    region_id: Optional[str] = Field(
        None, description="Optional region ID mapping to regions.id")



class UpdateCloudMessagingToken(BaseModel):
    token: str = Field(...,
                       description="The cloud messaging registration token")
    platform: str = Field(...,
                          description="The platform of the device (platform-ios|android)")


class AttachProviderService(BaseModel):
    service_id: str = Field(..., description="The ID of the service to add")


class UpdateRegion(BaseModel):
    region_id: Optional[str] = Field(None, description="The ID of the region to associate with the user")


class UpdateOnlineStatus(BaseModel):
    is_online: bool = Field(..., description="True to set provider online, False to set offline")


class LocationPing(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Latitude coordinate")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Longitude coordinate")


class ProviderAvailabilityBlock(BaseModel):
    day_of_week: int = Field(ge=1, le=7, description="1=Sunday, 7=Saturday")
    day_name: Optional[str] = Field(default=None, description="Name of day of week e.g. Sunday")
    start_time: time = Field(description="Start time (e.g., 07:00:00)")
    end_time: time = Field(description="End time (e.g., 18:00:00)")
    is_active: Optional[bool] = Field(default=True, description="Whether this availability block is active")


class ProviderAvailabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[str] = None
    provider_id: Optional[str] = None
    day_of_week: Optional[int] = Field(default=None, description="1=Sunday, 7=Saturday")
    day_name: Optional[str] = Field(default=None, description="Name of day of week e.g. Sunday")
    start_time: Optional[time] = Field(default=None, description="Start time (e.g., 07:00:00)")
    end_time: Optional[time] = Field(default=None, description="End time (e.g., 18:00:00)")
    is_active: Optional[bool] = Field(default=True, description="Whether this availability block is active")


class UpdateProviderAvailabilityBlock(BaseModel):
    day_of_week: Optional[int] = Field(default=None, ge=1, le=7, description="1=Sunday, 7=Saturday")
    start_time: Optional[time] = Field(default=None, description="Start time (e.g., 07:00:00)")
    end_time: Optional[time] = Field(default=None, description="End time (e.g., 18:00:00)")
    is_active: Optional[bool] = Field(default=None, description="Whether this availability block is active")



