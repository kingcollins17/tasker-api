"""Celery-specific async database engine and session factory.

Uses NullPool and per-event-loop engines to avoid sharing pooled
connections or asyncio locks across Celery worker threads/event loops.
Each Celery task creates and disposes its own connection via
``celery_session_factory``.

FastAPI keeps its own pooled engine in ``app.core.database``.
"""

import asyncio
from typing import Dict

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings

_engines: Dict[int, AsyncEngine] = {}


def get_celery_engine() -> AsyncEngine:
    """Get or create an AsyncEngine bound to the current event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

    loop_id = id(loop)
    if loop_id not in _engines:
        _engines[loop_id] = create_async_engine(
            settings.DATABASE_URL,
            echo=False,
            poolclass=NullPool,
        )
    return _engines[loop_id]


class CelerySessionFactory:
    """Callable session factory that returns an AsyncSession bound to the current loop's engine."""

    def __call__(self, **kwargs) -> AsyncSession:
        engine = get_celery_engine()
        factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            **kwargs,
        )
        return factory()


celery_session_factory = CelerySessionFactory()
