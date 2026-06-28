from typing import Generic, Optional, TypeVar
from pydantic import BaseModel

DataType = TypeVar("DataType")


class BaseAPIResponse(BaseModel, Generic[DataType]):
    """Generic API response structure wrapping the payload, message details, and HTTP status code."""

    data: Optional[DataType] = None
    detail: Optional[str] = None
    status_code: int = 200
