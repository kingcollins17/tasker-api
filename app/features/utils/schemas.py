from typing import List
from pydantic import BaseModel, Field


class FileUploadItemResponse(BaseModel):
    filename: str
    url: str


class MultipleFileUploadResponse(BaseModel):
    files: List[FileUploadItemResponse]
    total: int


class DeleteFilesRequest(BaseModel):
    file_urls: List[str] = Field(
        ..., min_length=1, description="List of file URLs to delete"
    )


class FileDeleteResult(BaseModel):
    file_url: str
    success: bool


class MultipleFileDeleteResponse(BaseModel):
    results: List[FileDeleteResult]
    deleted_count: int
