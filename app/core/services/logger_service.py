import sys
from typing import Any, AsyncGenerator, Dict, List, Optional
from fastapi import Depends
from sqlalchemy import desc, func, select
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.models.system_logs import LogLevel, SystemLog
from app.core.repository import GetRepository, QueryOptions, Repository

# External in-memory buffer shared across instances
_LOG_BUFFER: List[SystemLog] = []
DEFAULT_MAX_BUFFER_SIZE: int = 100


class LoggerService:
    def __init__(self, repository: Repository[SystemLog], max_buffer_size: int = DEFAULT_MAX_BUFFER_SIZE):
        self.repository = repository
        self.session: AsyncSession = repository.session
        self.max_buffer_size = max_buffer_size

    async def flush(self) -> None:
        """Flush all buffered log entries to the database using repository.bulk_add."""
        global _LOG_BUFFER
        if not _LOG_BUFFER:
            return
        logs_to_flush = list(_LOG_BUFFER)
        _LOG_BUFFER.clear()
        try:
            await self.repository.bulk_add(logs_to_flush)
        except Exception as log_exc:
            print(
                f"[LoggerService] Failed to flush bulk logs ({len(logs_to_flush)} entries): {log_exc}",
                file=sys.stderr,
            )
            try:
                await self.session.rollback()
            except Exception:
                pass

    async def _log(
        self,
        level: LogLevel,
        message: str,
        source: Optional[str] = None,
        duration_ms: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[SystemLog]:
        log_entry = SystemLog(
            level=level,
            message=message,
            source=source,
            duration_ms=duration_ms,
            metadata_=metadata,
        )
        _LOG_BUFFER.append(log_entry)
        if len(_LOG_BUFFER) >= self.max_buffer_size:
            await self.flush()
        return log_entry

    async def info(self, message: str, source: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> Optional[SystemLog]:
        return await self._log(LogLevel.INFO, message, source, metadata=metadata)

    async def warn(self, message: str, source: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> Optional[SystemLog]:
        return await self._log(LogLevel.WARN, message, source, metadata=metadata)

    async def error(self, message: str, source: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> Optional[SystemLog]:
        return await self._log(LogLevel.ERROR, message, source, metadata=metadata)
        
    async def debug(self, message: str, source: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> Optional[SystemLog]:
        return await self._log(LogLevel.DEBUG, message, source, metadata=metadata)

    async def metric(self, message: str, duration_ms: int, source: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> Optional[SystemLog]:
        return await self._log(LogLevel.METRIC, message, source, duration_ms=duration_ms, metadata=metadata)

    async def get_logs(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> List[SystemLog]:
        options = QueryOptions(
            filters=filters or {},
            limit=limit,
            offset=offset,
            order_by=order_by,
            descending=descending,
        )
        return await self.repository.get_all(options)

    async def get_stats(self) -> Dict[str, int]:
        statement = select(col(SystemLog.level), func.count(col(SystemLog.id))).group_by(col(SystemLog.level))
        result = await self.repository.execute(statement)
        stats = {row[0].value: row[1] for row in result.all()}
        return stats

    async def get_metrics_summary(self) -> List[Dict[str, Any]]:
        statement = (
            select(
                col(SystemLog.source),
                func.count(col(SystemLog.id)).label("count"),
                func.avg(col(SystemLog.duration_ms)).label("avg_duration"),
                func.max(col(SystemLog.duration_ms)).label("max_duration"),
                func.min(col(SystemLog.duration_ms)).label("min_duration")
            )
            .where(col(SystemLog.level) == LogLevel.METRIC)
            .group_by(col(SystemLog.source))
        )
        result = await self.repository.execute(statement)
        
        summary = []
        for row in result.all():
            summary.append({
                "source": row[0],
                "count": row[1],
                "avg_duration": float(row[2]) if row[2] else 0.0,
                "max_duration": row[3],
                "min_duration": row[4],
            })
        return summary


SystemLogger = LoggerService


async def get_logger_service() -> AsyncGenerator[LoggerService, None]:
    """FastAPI dependency for LoggerService with an independent database session."""
    from app.core.database import async_session_factory
    async with async_session_factory() as session:
        repository = Repository(SystemLog, session)
        service = LoggerService(repository)
        try:
            yield service
        finally:
            await service.flush()


def get_logger_service_manual(session: AsyncSession) -> LoggerService:
    """Manually creates a LoggerService instance for contexts without FastAPI Depends (e.g., Celery)."""
    repository = Repository(SystemLog, session)
    return LoggerService(repository)
