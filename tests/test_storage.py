import io
import pytest
from fastapi import UploadFile
from app.core.services.storage import StorageService, MockStorageService, get_storage_service

def test_storage_service_is_abstract():
    # Attempting to instantiate the abstract class directly should raise a TypeError
    with pytest.raises(TypeError):
        StorageService()  # type: ignore


@pytest.mark.asyncio
async def test_mock_storage_service_upload():
    # Arrange
    service = MockStorageService(base_url="https://test-bucket.s3.amazonaws.com")
    
    file_content = b"fake file content"
    file_like = io.BytesIO(file_content)
    upload_file = UploadFile(file=file_like, filename="avatar.png")
    
    # Act
    url = await service.upload_file(upload_file)
    
    # Assert
    assert url.startswith("https://images.unsplash.com/")


@pytest.mark.asyncio
async def test_mock_storage_service_upload_default_url():
    # Arrange
    service = MockStorageService()
    file_like = io.BytesIO(b"")
    upload_file = UploadFile(file=file_like, filename=None)  # Test no filename case
    
    # Act
    url = await service.upload_file(upload_file)
    
    # Assert
    assert url.startswith("https://images.unsplash.com/")


def test_get_storage_service_dependency():
    # Act
    service = get_storage_service()
    
    # Assert
    assert isinstance(service, StorageService)
    assert isinstance(service, MockStorageService)


@pytest.mark.asyncio
async def test_mock_storage_service_delete():
    # Arrange
    service = MockStorageService()
    file_url = "https://mock-storage.local/1234abcd_avatar.png"
    
    # Act
    result = await service.delete_file(file_url)
    
    # Assert
    assert result is True


@pytest.mark.asyncio
async def test_mock_storage_service_upload_bytes():
    service = MockStorageService()
    file_bytes = b"hello world"
    
    # Upload without explicit filename
    url_no_name = await service.upload_file(file_bytes)
    assert url_no_name.startswith("https://images.unsplash.com/")
    
    # Upload with explicit filename
    url_with_name = await service.upload_file(file_bytes, filename="hello.txt")
    assert url_with_name.startswith("https://images.unsplash.com/")


@pytest.mark.asyncio
async def test_mock_storage_service_upload_binary_io():
    service = MockStorageService()
    
    # Mocking a standard open file object which has a "name" property
    class MockFileLike(io.BytesIO):
        name = "/path/to/my_document.pdf"
        
    file_obj = MockFileLike(b"pdf contents")
    
    url = await service.upload_file(file_obj)
    assert url.startswith("https://images.unsplash.com/")



