from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings

# Create the async database engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # Can be set to True for debugging SQL queries
    future=True,
)

# Create an async session maker configured to produce AsyncSession instances
async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency function to obtain an AsyncSession object.

    Yields:
        AsyncSession: A database session context managed for a single request.
    """
    async with async_session_maker() as session:
        yield session
