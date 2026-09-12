from typing import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import app.core.models  # noqa: F401
from app.core.config import settings

# Create the async database engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    # future=True,
    pool_size=settings.POOL_SIZE,
    max_overflow=1,
    pool_pre_ping=True,
)

# Create an async session factory configured to produce AsyncSession instances
async_session_factory = async_sessionmaker(
    engine,
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
    async with async_session_factory() as session:
        yield session


async def init_db() -> None:
    """Initialize database tables defined in models on app startup."""
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
    except Exception:
        pass

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE payout_queue ADD COLUMN IF NOT EXISTS lock_version INT NOT NULL DEFAULT 1;")
            )
    except Exception:
        pass

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)




