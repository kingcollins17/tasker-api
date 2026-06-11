import pytest
from unittest.mock import patch, MagicMock
from sqlmodel.ext.asyncio.session import AsyncSession
from app.core.database import get_session, engine, async_session_maker

def test_database_setup():
    """Verify that engine and session maker are correctly initialized."""
    assert engine is not None
    assert async_session_maker is not None

@pytest.mark.anyio
async def test_get_session_dependency():
    """Verify that get_session correctly yields an AsyncSession and manages its lifecycle."""
    with patch("app.core.database.async_session_maker") as mock_session_maker:
        mock_session = MagicMock(spec=AsyncSession)
        
        # Mock the async context manager behavior of async_session_maker()
        mock_context = MagicMock()
        
        # In python, async context managers have __aenter__ and __aexit__ which are coroutines
        async def aenter(*args, **kwargs):
            return mock_session
            
        async def aexit(*args, **kwargs):
            return False
            
        mock_context.__aenter__ = aenter
        mock_context.__aexit__ = aexit
        mock_session_maker.return_value = mock_context
        
        # Consume the generator
        sessions = []
        async for session in get_session():
            sessions.append(session)
            
        assert len(sessions) == 1
        assert sessions[0] == mock_session
        mock_session_maker.assert_called_once()
