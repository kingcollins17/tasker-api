from pydantic import BaseModel
from typing import Optional

class MinimalProviderResponse(BaseModel):
    id: Optional[str] = None
    fullname: Optional[str] = None
    email: Optional[str] = None
    average_ratings: Optional[float] = None
    credibility_score: Optional[float] = None
    gender: Optional[str] = None
