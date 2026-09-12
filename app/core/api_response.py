from typing import Generic, Optional, TypeVar, List
from pydantic import BaseModel

DataType = TypeVar("DataType")


class BaseAPIResponse(BaseModel, Generic[DataType]):
    """Generic API response structure wrapping the payload, message details, and HTTP status code."""

    data: Optional[DataType] = None
    detail: Optional[str] = None
    message: Optional[str] = None
    status_code: int = 200

    @classmethod
    def success_response(
        cls,
        data: Optional[DataType] = None,
        message: Optional[str] = None,
        detail: Optional[str] = None,
        status_code: int = 200,
    ) -> "BaseAPIResponse[DataType]":
        msg = message or detail
        return cls(
            data=data,
            message=msg,
            detail=msg,
            status_code=status_code,
        )

    @classmethod
    def error_response(
        cls,
        message: Optional[str] = None,
        detail: Optional[str] = None,
        status_code: int = 400,
        data: Optional[DataType] = None,
    ) -> "BaseAPIResponse[DataType]":
        msg = message or detail
        return cls(
            data=data,
            message=msg,
            detail=msg,
            status_code=status_code,
        )


class PaginatedData(BaseModel, Generic[DataType]):
    """Generic pagination structure wrapper for multiple records."""
    
    items: Optional[List[DataType]] = None
    total: Optional[int] = None
    page: Optional[int] = None
    per_page: Optional[int] = None
