from typing import Generic, Optional, TypeVar, List
from pydantic import BaseModel

DataType = TypeVar("DataType")


class BaseAPIResponse(BaseModel, Generic[DataType]):
    """Generic API response structure wrapping the payload, message details, and HTTP status code."""

    data: Optional[DataType] = None
    detail: Optional[str] = None
    status_code: int = 200

class PaginatedData(BaseModel, Generic[DataType]):
    """Generic pagination structure wrapper for multiple records."""
    
    items: Optional[List[DataType]] = None
    total: Optional[int] = None
    page: Optional[int] = None
    per_page: Optional[int] = None
