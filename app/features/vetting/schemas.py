from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from app.core.models.users import MediaType

# Quiz Schemas
class QuizAnswerSubmit(BaseModel):
    answers: Dict[str, str] = Field(..., description="Mapping of question_id to selected option key (e.g. 'A')")

class QuizQuestionResponse(BaseModel):
    id: Optional[str] = None
    question_text: Optional[str] = None
    options: Optional[Dict[str, str]] = None

class QuizResultResponse(BaseModel):
    score_percentage: Optional[float] = None
    passed: Optional[bool] = None

# Admin Quiz Schemas
class QuizQuestionCreate(BaseModel):
    category_id: str = Field(..., description="Service category ID")
    question_text: str = Field(..., description="The actual question text")
    options: Dict[str, str] = Field(..., description="JSON mapping options e.g. {'A': 'Option 1', 'B': 'Option 2'}")
    correct_option: str = Field(..., description="The correct option key e.g. 'A'")

class QuizQuestionUpdate(BaseModel):
    category_id: Optional[str] = Field(None, description="Service category ID")
    question_text: Optional[str] = Field(None, description="The actual question text")
    options: Optional[Dict[str, str]] = Field(None, description="JSON mapping options e.g. {'A': 'Option 1', 'B': 'Option 2'}")
    correct_option: Optional[str] = Field(None, description="The correct option key e.g. 'A'")

class AdminQuizQuestionResponse(BaseModel):
    id: Optional[str] = None
    category_id: Optional[str] = None
    question_text: Optional[str] = None
    options: Optional[Dict[str, str]] = None
    correct_option: Optional[str] = None
    created_at: Optional[datetime] = None

class QuizQuestionItemCreate(BaseModel):
    question_text: str = Field(..., description="The actual question text")
    options: Dict[str, str] = Field(..., description="JSON mapping options e.g. {'A': 'Option 1', 'B': 'Option 2'}")
    correct_option: str = Field(..., description="The correct option key e.g. 'A'")

class QuizCreateRequest(BaseModel):
    category_id: str = Field(..., description="Service category ID for the quiz questions")
    questions: List[QuizQuestionItemCreate] = Field(..., description="List of question items to create")

class BulkDeleteQuestionsRequest(BaseModel):
    question_ids: List[str] = Field(..., description="List of question IDs to delete")

class QuizQuestionBulkUpdateItem(BaseModel):
    id: str = Field(..., description="ID of the question to update")
    category_id: Optional[str] = Field(None, description="Service category ID")
    question_text: Optional[str] = Field(None, description="The actual question text")
    options: Optional[Dict[str, str]] = Field(None, description="JSON mapping options")
    correct_option: Optional[str] = Field(None, description="The correct option key")

class BulkUpdateQuestionsRequest(BaseModel):
    questions: List[QuizQuestionBulkUpdateItem] = Field(..., description="List of question update payloads")

class BulkOperationResponse(BaseModel):
    affected_count: Optional[int] = None
    message: Optional[str] = None


# Portfolio Schemas
class PortfolioUploadRequest(BaseModel):
    category_id: Optional[str] = Field(None, description="Optional category ID to link the media to")
    media_url: str = Field(..., description="URL of the uploaded media")
    media_type: MediaType = Field(..., description="Type of the uploaded media")

class PortfolioMediaResponse(BaseModel):
    id: Optional[str] = None
    media_url: Optional[str] = None
    media_type: Optional[MediaType] = None
    status: Optional[str] = None

# Guarantor Schemas
class AddGuarantorRequest(BaseModel):
    guarantor_name: str = Field(..., description="Full name of the guarantor")
    guarantor_phone: str = Field(..., description="Phone number of the guarantor")
    relationship: Optional[str] = Field(None, description="Relationship to the provider")

class AddGuarantorResponse(BaseModel):
    id: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None

