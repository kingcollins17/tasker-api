from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any
from app.core.models.users import PaymentProvider


class MinimalProviderResponse(BaseModel):
    id: Optional[str] = None
    fullname: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    average_ratings: Optional[float] = None
    credibility_score: Optional[float] = None
    gender: Optional[str] = None
    profile_picture_url: Optional[str] = None


class MinimalCustomerResponse(BaseModel):
    id: Optional[str] = None
    fullname: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    average_ratings: Optional[float] = None

    credibility_score: Optional[float] = None
    gender: Optional[str] = None


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
