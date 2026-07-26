from typing import List, Optional, Any, Dict
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.models.system_logs import SystemLog, LogLevel
from app.core.services.logger_service import LoggerService, get_logger_service

router = APIRouter()

@router.get("/logs")
async def get_system_logs(
    level: Optional[LogLevel] = Query(None, description="Filter by log level"),
    source: Optional[str] = Query(None, description="Filter by source"),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=1000),
    logger: LoggerService = Depends(get_logger_service)
) -> List[SystemLog]:
    try:
        filters = {}
        if level:
            filters["level"] = level
        if source:
            filters["source"] = source
            
        limit = per_page
        offset = (page - 1) * per_page
        return await logger.get_logs(filters=filters, limit=limit, offset=offset)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/logs/stats")
async def get_log_stats(
    logger: LoggerService = Depends(get_logger_service)
) -> Dict[str, int]:
    try:
        return await logger.get_stats()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/logs/metrics")
async def get_metrics_summary(
    logger: LoggerService = Depends(get_logger_service)
) -> List[Dict[str, Any]]:
    try:
        return await logger.get_metrics_summary()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
