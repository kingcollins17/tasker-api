import asyncio
from typing import List, Union
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.api_response import BaseAPIResponse
from app.core.deps import GetCurrentUserOrAdmin
from app.core.error_handler import AppErrorHandler
from app.core.models.admins import AdminUser
from app.core.services.storage import StorageService, get_storage_service
from app.features.users.schemas import UserResponse
from app.features.utils.schemas import (
    DeleteFilesRequest,
    FileDeleteResult,
    FileUploadItemResponse,
    MultipleFileDeleteResponse,
    MultipleFileUploadResponse,
)

router = APIRouter(prefix="/utils", tags=["Utils"])


@router.post(
    "/upload-file",
    response_model=BaseAPIResponse[FileUploadItemResponse],
    status_code=status.HTTP_200_OK,
)
async def upload_single_file(
    file: UploadFile = File(..., description="Single file to upload"),
    current_user_or_admin: Union[UserResponse, AdminUser] = Depends(
        GetCurrentUserOrAdmin()
    ),
    storage_service: StorageService = Depends(get_storage_service),
):
    """Upload a single file. Accessible by authenticated users and admins."""
    try:
        file_url = await storage_service.upload_file(file)
        filename = file.filename or "uploaded_file"
        data = FileUploadItemResponse(filename=filename, url=file_url)
        return BaseAPIResponse.success_response(
            data=data,
            message="File uploaded successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload file",
        )


@router.post(
    "/upload-files",
    response_model=BaseAPIResponse[MultipleFileUploadResponse],
    status_code=status.HTTP_200_OK,
)
async def upload_multiple_files(
    files: List[UploadFile] = File(..., description="Multiple files to upload"),
    current_user_or_admin: Union[UserResponse, AdminUser] = Depends(
        GetCurrentUserOrAdmin()
    ),
    storage_service: StorageService = Depends(get_storage_service),
):
    """Upload multiple files in parallel. Accessible by authenticated users and admins."""
    try:
        async def _upload_single_file(file: UploadFile) -> FileUploadItemResponse:
            file_url = await storage_service.upload_file(file)
            filename = file.filename or "uploaded_file"
            return FileUploadItemResponse(filename=filename, url=file_url)

        uploaded_files: List[FileUploadItemResponse] = list(
            await asyncio.gather(*[_upload_single_file(f) for f in files])
        )

        data = MultipleFileUploadResponse(
            files=uploaded_files,
            total=len(uploaded_files),
        )
        return BaseAPIResponse.success_response(
            data=data,
            message="Files uploaded successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload files",
        )


@router.post(
    "/delete-files",
    response_model=BaseAPIResponse[MultipleFileDeleteResponse],
    status_code=status.HTTP_200_OK,
)
async def delete_multiple_files(
    body: DeleteFilesRequest,
    current_user_or_admin: Union[UserResponse, AdminUser] = Depends(
        GetCurrentUserOrAdmin()
    ),
    storage_service: StorageService = Depends(get_storage_service),
):
    """Delete multiple files in parallel. Accessible by authenticated users and admins."""
    try:
        async def _delete_single_file(file_url: str) -> FileDeleteResult:
            success = await storage_service.delete_file(file_url)
            return FileDeleteResult(file_url=file_url, success=success)

        results: List[FileDeleteResult] = list(
            await asyncio.gather(*[_delete_single_file(url) for url in body.file_urls])
        )
        deleted_count = sum(1 for r in results if r.success)

        data = MultipleFileDeleteResponse(
            results=results,
            deleted_count=deleted_count,
        )
        return BaseAPIResponse.success_response(
            data=data,
            message="Files deleted successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete files",
        )


@router.delete(
    "/delete-files",
    response_model=BaseAPIResponse[MultipleFileDeleteResponse],
    status_code=status.HTTP_200_OK,
)
async def delete_multiple_files_via_delete_method(
    body: DeleteFilesRequest,
    current_user_or_admin: Union[UserResponse, AdminUser] = Depends(
        GetCurrentUserOrAdmin()
    ),
    storage_service: StorageService = Depends(get_storage_service),
):
    """Delete multiple files in parallel via DELETE method. Accessible by authenticated users and admins."""
    try:
        async def _delete_single_file(file_url: str) -> FileDeleteResult:
            success = await storage_service.delete_file(file_url)
            return FileDeleteResult(file_url=file_url, success=success)

        results: List[FileDeleteResult] = list(
            await asyncio.gather(*[_delete_single_file(url) for url in body.file_urls])
        )
        deleted_count = sum(1 for r in results if r.success)

        data = MultipleFileDeleteResponse(
            results=results,
            deleted_count=deleted_count,
        )
        return BaseAPIResponse.success_response(
            data=data,
            message="Files deleted successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete files",
        )
