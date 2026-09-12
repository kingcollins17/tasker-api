from typing import Optional
from pydantic import BaseModel, Field


# Guarantor Schemas
class AddGuarantorRequest(BaseModel):
    guarantor_name: str = Field(..., description="Full name of the guarantor")
    guarantor_phone: str = Field(..., description="Phone number of the guarantor")
    relationship: Optional[str] = Field(None, description="Relationship to the provider")


class AddGuarantorResponse(BaseModel):
    id: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None


