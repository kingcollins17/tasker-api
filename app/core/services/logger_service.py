from typing import Optional, Dict, Any, List
from sqlalchemy import select, func, desc
from sqlmodel.ext.asyncio.session import AsyncSession
from fastapi import Depends
from app.core.models.system_logs import SystemLog, LogLevel
from app.core.repository import Repository, QueryOptions, GetRepository

class LoggerService:
    def __init__(self, repository: Repository[SystemLog]):
        self.repository = repository
        self.session: AsyncSession = repository.session

    async def _log(
        self,
        level: LogLevel,
        message: str,
        source: Optional[str] = None,
        duration_ms: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SystemLog:
        log_entry = SystemLog(
            level=level,
            message=message,
            source=source,
            duration_ms=duration_ms,
            metadata_=metadata,
        )
        return await self.repository.add(log_entry)

    async def info(self, message: str, source: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> SystemLog:
        return await self._log(LogLevel.INFO, message, source, metadata=metadata)

    async def warn(self, message: str, source: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> SystemLog:
        return await self._log(LogLevel.WARN, message, source, metadata=metadata)

    async def error(self, message: str, source: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> SystemLog:
        return await self._log(LogLevel.ERROR, message, source, metadata=metadata)
        
    async def debug(self, message: str, source: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> SystemLog:
        return await self._log(LogLevel.DEBUG, message, source, metadata=metadata)

    async def metric(self, message: str, duration_ms: int, source: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> SystemLog:
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
        statement = select(SystemLog.level, func.count(SystemLog.id)).group_by(SystemLog.level)
        result = await self.repository.execute(statement)
        stats = {row[0].value: row[1] for row in result.all()}
        return stats

    async def get_metrics_summary(self) -> List[Dict[str, Any]]:
        statement = (
            select(
                SystemLog.source,
                func.count(SystemLog.id).label("count"),
                func.avg(SystemLog.duration_ms).label("avg_duration"),
                func.max(SystemLog.duration_ms).label("max_duration"),
                func.min(SystemLog.duration_ms).label("min_duration")
            )
            .where(SystemLog.level == LogLevel.METRIC)
            .group_by(SystemLog.source)
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

def get_logger_service(
    repository: Repository[SystemLog] = Depends(GetRepository(SystemLog))
) -> LoggerService:
    """FastAPI dependency for LoggerService."""
    return LoggerService(repository)

def get_logger_service_manual(session: AsyncSession) -> LoggerService:
    """Manually creates a LoggerService instance for contexts without FastAPI Depends (e.g., Celery)."""
    repository = Repository(SystemLog, session)
    return LoggerService(repository)
