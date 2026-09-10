"""Celery-specific async database engine and session factory.

Uses NullPool to avoid sharing pooled connections across Celery worker
threads/event loops.  Each Celery task creates and disposes its own
connection via ``celery_session_factory``.

FastAPI keeps its own pooled engine in ``app.core.database``.
"""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings

celery_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)

celery_session_factory = async_sessionmaker(
    celery_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
