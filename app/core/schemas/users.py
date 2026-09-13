from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any
from datetime import datetime
from app.core.models.users import PaymentProvider


class UserLocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    user_id: Optional[str] = None
    region_id: Optional[str] = None
    address_line: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class UserStatsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    user_id: Optional[str] = None
    credibility_score: Optional[float] = 25.0
    average_ratings: Optional[float] = 0.0
    total_ratings: Optional[int] = 0
    acceptance_rate_30d: Optional[float] = 100.0
    completion_rate_30d: Optional[float] = 100.0
    current_tier: Optional[int] = 1
    total_tasks_completed: Optional[int] = 0
    total_tasks_posted: Optional[int] = 0
    consecutive_declines: Optional[int] = 0
    cancellation_count: Optional[int] = 0


class MinimalProviderResponse(BaseModel):
    id: Optional[str] = None
    fullname: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    average_ratings: Optional[float] = None
    credibility_score: Optional[float] = None
    gender: Optional[str] = None
    profile_picture_url: Optional[str] = None
    selfie_url: Optional[str] = None
    total_tasks_completed: Optional[int] = None
    location: Optional[UserLocationResponse] = None

    @classmethod
    def from_user(cls, user: Any) -> Optional["MinimalProviderResponse"]:
        if not user:
            return None
        fullname = None
        gender = None
        selfie_url = None
        total_tasks_completed = None
        if getattr(user, "provider_profile", None):
            first_name = user.provider_profile.first_name or ""
            last_name = user.provider_profile.last_name or ""
            fullname = f"{first_name} {last_name}".strip() or None
            gender = user.provider_profile.gender
            selfie_url = user.provider_profile.selfie_url

        stats = getattr(user, "stats", None)
        avg_ratings = stats.average_ratings if stats else getattr(user, "average_ratings", 0.0)
        cred_score = stats.credibility_score if stats else getattr(user, "credibility_score", 25.0)
        total_tasks = stats.total_tasks_completed if stats else 0

        return cls(
            id=user.id,
            email=user.email,
            phone_number=user.phone_number,
            fullname=fullname,
            average_ratings=avg_ratings,
            credibility_score=cred_score,
            gender=gender,
            profile_picture_url=selfie_url,
            selfie_url=selfie_url,
            total_tasks_completed=total_tasks,
        )


class MinimalCustomerResponse(BaseModel):
    id: Optional[str] = None
    fullname: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    average_ratings: Optional[float] = None
    credibility_score: Optional[float] = None
    gender: Optional[str] = None

    @classmethod
    def from_user(cls, user: Any) -> Optional["MinimalCustomerResponse"]:
        if not user:
            return None
        fullname = None
        if getattr(user, "customer_profile", None):
            first_name = user.customer_profile.first_name or ""
            last_name = user.customer_profile.last_name or ""
            fullname = f"{first_name} {last_name}".strip() or None

        stats = getattr(user, "stats", None)
        avg_ratings = stats.average_ratings if stats else getattr(user, "average_ratings", 0.0)
        cred_score = stats.credibility_score if stats else getattr(user, "credibility_score", 25.0)

        return cls(
            id=user.id,
            email=user.email,
            phone_number=user.phone_number,
            fullname=fullname,
            average_ratings=avg_ratings,
            credibility_score=cred_score,
            gender=None,
        )



class BankResponse(BaseModel):
    id: Optional[str] = None
    bank_code: Optional[str] = None
    name: Optional[str] = None
    logo_url: Optional[str] = None


class PaymentAccountBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    provider: PaymentProvider
    external_account_id: Optional[str] = None
    account_name: Optional[str] = None
    account_metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

class PaymentAccountCreate(BaseModel):
    bank_code: str
    bank_name: str
    account_name: str
    account_number: str

class PaymentAccountUpdate(BaseModel):
    provider: Optional[PaymentProvider] = None
    external_account_id: Optional[str] = None
    account_name: Optional[str] = None
    account_metadata: Optional[Dict[str, Any]] = None


class PaymentAccountResponse(PaymentAccountBase):
    id: str
    user_id: str
    is_active: bool

class BankAccountVerificationResponse(BaseModel):
    account_number: Optional[str] = None
    account_name: Optional[str] = None
    bank_name: Optional[str] = None
    bank_code: Optional[str] = None
