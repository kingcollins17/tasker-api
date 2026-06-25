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
    assert url.startswith("https://test-bucket.s3.amazonaws.com/")
    assert "avatar.png" in url
    # Ensure there's a unique prefix
    parts = url.split("/")[-1].split("_")
    assert len(parts) >= 2
    assert len(parts[0]) == 8  # hex uuid substring length we defined


@pytest.mark.asyncio
async def test_mock_storage_service_upload_default_url():
    # Arrange
    service = MockStorageService()
    file_like = io.BytesIO(b"")
    upload_file = UploadFile(file=file_like, filename=None)  # Test no filename case
    
    # Act
    url = await service.upload_file(upload_file)
    
    # Assert
    assert url.startswith("https://mock-storage.local/")
    assert "uploaded_file" in url


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
    assert "uploaded_file" in url_no_name
    
    # Upload with explicit filename
    url_with_name = await service.upload_file(file_bytes, filename="hello.txt")
    assert "hello.txt" in url_with_name


@pytest.mark.asyncio
async def test_mock_storage_service_upload_binary_io():
    service = MockStorageService()
    
    # Mocking a standard open file object which has a "name" property
    class MockFileLike(io.BytesIO):
        name = "/path/to/my_document.pdf"
        
    file_obj = MockFileLike(b"pdf contents")
    
    url = await service.upload_file(file_obj)
    assert "my_document.pdf" in url


