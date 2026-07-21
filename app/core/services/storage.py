from abc import ABC, abstractmethod
import uuid
import os
import random
from typing import Union, BinaryIO, Optional
from fastapi import UploadFile


class StorageService(ABC):
    """Abstract base class representing a file storage service."""

    @abstractmethod
    async def upload_file(
        self, file: Union[UploadFile, bytes, BinaryIO], filename: Optional[str] = None
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
        self, file: Union[UploadFile, bytes, BinaryIO], filename: Optional[str] = None
    ) -> str:
        """Simulates uploading a file by generating a mock URL.

        Args:
            file: The file to mock upload.
            filename: Optional filename to use.

        Returns:
            str: A mock URL pointing to a random Unsplash image.
        """
        mock_image_urls = [
            "https://images.unsplash.com/photo-1522202176988-66273c2fd55f?q=80&w=1742&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1498050108023-c5249f4df085?q=80&w=1744&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1504384308090-c894fdcc538d?q=80&w=1740&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1517245386807-bb43f82c33c4?q=80&w=1740&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1555066931-4365d14bab8c?q=80&w=1740&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?q=80&w=1768&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?q=80&w=1740&auto=format&fit=crop",
        ]
        return random.choice(mock_image_urls)

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
