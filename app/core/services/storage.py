from abc import ABC, abstractmethod
import uuid
import os
from typing import Union, BinaryIO, Optional
from fastapi import UploadFile

class StorageService(ABC):
    """Abstract base class representing a file storage service."""

    @abstractmethod
    async def upload_file(
        self,
        file: Union[UploadFile, bytes, BinaryIO],
        filename: Optional[str] = None
    ) -> str:
        """Uploads a file and returns its URL.

        Args:
            file: The file to upload (FastAPI UploadFile, raw bytes, or BinaryIO object).
            filename: Optional filename to use, especially if file is bytes or BinaryIO.

        Returns:
            str: The public URL of the uploaded file.
        """
        pass

    @abstractmethod
    async def delete_file(self, file_url: str) -> bool:
        """Deletes a file from storage given its URL.

        Args:
            file_url: The public URL of the file to delete.

        Returns:
            bool: True if deletion was successful, False otherwise.
        """
        pass


class MockStorageService(StorageService):
    """Mock implementation of the StorageService interface.

    Returns a mock URL instead of actually uploading the file.
    """

    def __init__(self, base_url: str = "https://mock-storage.local"):
        self.base_url = base_url

    async def upload_file(
        self,
        file: Union[UploadFile, bytes, BinaryIO],
        filename: Optional[str] = None
    ) -> str:
        """Simulates uploading a file by generating a mock URL.

        Args:
            file: The file to mock upload.
            filename: Optional filename to use.

        Returns:
            str: A mock URL containing a unique prefix and the original filename.
        """
        resolved_filename = filename
        
        if not resolved_filename:
            if isinstance(file, UploadFile):
                resolved_filename = file.filename
            elif hasattr(file, "name"):
                # File-like object (e.g. open file descriptor)
                resolved_filename = os.path.basename(file.name)
        
        if not resolved_filename:
            resolved_filename = "uploaded_file"
        
        # Avoid collisions by prefixing with a truncated random UUID
        unique_prefix = uuid.uuid4().hex[:8]
        mock_filename = f"{unique_prefix}_{resolved_filename}"
        
        return f"{self.base_url}/{mock_filename}"

    async def delete_file(self, file_url: str) -> bool:
        """Simulates deleting a file.

        Args:
            file_url: The public URL of the file to delete.

        Returns:
            bool: True to simulate a successful deletion.
        """
        from app.core.logging import logger
        logger.info(f"[MOCK STORAGE] Deleting file: {file_url}")
        return True


# Dependency provider
def get_storage_service() -> StorageService:
    """Dependency provider function for StorageService.

    Returns a MockStorageService instance.
    """
    return MockStorageService()
