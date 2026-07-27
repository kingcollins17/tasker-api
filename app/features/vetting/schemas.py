from typing import Optional
from pydantic import BaseModel, Field
from typing import List, Dict
from app.core.models.users import MediaType

# Quiz Schemas
class QuizAnswerSubmit(BaseModel):
    answers: Dict[str, str] = Field(..., description="Mapping of question_id to selected option key (e.g. 'A')")

class QuizQuestionResponse(BaseModel):
    id: str
    question_text: str
    options: Dict[str, str]

class QuizResultResponse(BaseModel):
    score_percentage: float
    passed: bool

# Portfolio Schemas
class PortfolioUploadRequest(BaseModel):
    category_id: Optional[str] = Field(None, description="Optional category ID to link the media to")
    media_url: str = Field(..., description="URL of the uploaded media")
    media_type: MediaType = Field(..., description="Type of the uploaded media")

class PortfolioMediaResponse(BaseModel):
    id: str
    media_url: str
    media_type: MediaType
    status: str

# Guarantor Schemas
class AddGuarantorRequest(BaseModel):
    guarantor_name: str = Field(..., description="Full name of the guarantor")
    guarantor_phone: str = Field(..., description="Phone number of the guarantor")
    relationship: Optional[str] = Field(None, description="Relationship to the provider")

class AddGuarantorResponse(BaseModel):
    id: str
    status: str
    message: str
