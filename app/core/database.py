from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel import SQLModel
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


async def init_db() -> None:
    """Initialize database tables defined in models on app startup."""
    # Import models to register them on SQLModel.metadata
    import app.core.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

