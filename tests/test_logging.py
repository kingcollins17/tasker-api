import logging
import pytest
from unittest.mock import patch
from app.core.logging import log_error

@log_error(reraise=False)
def sync_fail():
    raise ValueError("sync failure")

@log_error(reraise=True)
def sync_fail_reraise():
    raise TypeError("sync reraise")

@log_error(reraise=False)
async def async_fail():
    raise ValueError("async failure")

@log_error(reraise=True)
async def async_fail_reraise():
    raise TypeError("async reraise")

def test_sync_log_error():
    """Verify that sync functions have their exceptions logged and swallowed when reraise=False."""
    with patch("app.core.logging.logger.log") as mock_log:
        result = sync_fail()
        assert result is None
        mock_log.assert_called_once()
        args, _ = mock_log.call_args
        assert args[0] == logging.ERROR
        assert "ValueError: sync failure" in args[1]
        assert "sync_fail" in args[1]

def test_sync_log_error_reraise():
    """Verify that sync functions have their exceptions logged and re-raised when reraise=True."""
    with patch("app.core.logging.logger.log") as mock_log:
        with pytest.raises(TypeError):
            sync_fail_reraise()
        mock_log.assert_called_once()

@pytest.mark.anyio
async def test_async_log_error():
    """Verify that async functions have their exceptions logged and swallowed when reraise=False."""
    with patch("app.core.logging.logger.log") as mock_log:
        result = await async_fail()
        assert result is None
        mock_log.assert_called_once()
        args, _ = mock_log.call_args
        assert args[0] == logging.ERROR
        assert "ValueError: async failure" in args[1]
        assert "async_fail" in args[1]

@pytest.mark.anyio
async def test_async_log_error_reraise():
    """Verify that async functions have their exceptions logged and re-raised when reraise=True."""
    with patch("app.core.logging.logger.log") as mock_log:
        with pytest.raises(TypeError):
            await async_fail_reraise()
        mock_log.assert_called_once()
