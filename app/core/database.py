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
    pool_pre_ping=True,  # Checks if the connection is alive before using it
)

# Create an async session maker configured to produce AsyncSession instances
async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency function to obtain an AsyncSession object.

    Note:
        FastAPI caches dependency results per request by default. If multiple
        repositories or services depend on get_session within the same request,
        they will share the exact same AsyncSession instance, ensuring consistent
        database transactions across components.

    Yields:
        AsyncSession: A database session context managed for a single request.
    """
    async with async_session_maker() as session:
        yield session


async def init_db() -> None:
    """Initialize database tables defined in models on app startup."""
    # Import models to register them on SQLModel.metadata
    import app.core.models  # noqa: F401

    from sqlalchemy import text

    async with engine.begin() as conn:
        # Create PostGIS extension if it doesn't exist (required for geometry types)
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
        except Exception:
            # Ignore exceptions (e.g. concurrent creation race conditions or pre-existing extension)
            pass
        await conn.run_sync(SQLModel.metadata.create_all)

