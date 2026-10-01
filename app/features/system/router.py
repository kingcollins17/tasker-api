from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.error_handler import AppErrorHandler
from app.core.models.system_logs import LogLevel
from app.core.services.logger_service import LoggerService, get_logger_service
from app.features.system.schemas import (
    LogMetricSummaryResponse,
    LogStatsResponse,
    SystemLogResponse,
)

router = APIRouter()


@router.get(
    "/logs",
    response_model=BaseAPIResponse[PaginatedData[SystemLogResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_system_logs(
    level: Optional[LogLevel] = Query(None, description="Filter by log level"),
    source: Optional[str] = Query(None, description="Filter by source"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(100, ge=1, le=1000, description="Items per page"),
    logger: LoggerService = Depends(get_logger_service),
):
    """Retrieve system logs with optional filtering and pagination."""
    try:
        filters = {}
        if level:
            filters["level"] = level
        if source:
            filters["source"] = source

        limit = per_page
        offset = (page - 1) * per_page
        logs = await logger.get_logs(filters=filters, limit=limit, offset=offset)
        total = await logger.get_logs_count(filters=filters)

        items = [SystemLogResponse.model_validate(log) for log in logs]
        paginated_data = PaginatedData[SystemLogResponse](
            items=items,
            total=total,
            page=page,
            per_page=per_page,
        )
        return BaseAPIResponse.success_response(
            data=paginated_data,
            message="System logs retrieved successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving system logs.",
        )


@router.get(
    "/logs/stats",
    response_model=BaseAPIResponse[LogStatsResponse],
    status_code=status.HTTP_200_OK,
)
async def get_log_stats(
    logger: LoggerService = Depends(get_logger_service),
):
    """Retrieve system log statistics grouped by log level."""
    try:
        stats_dict = await logger.get_stats()
        stats_data = LogStatsResponse(
            info=stats_dict.get("INFO", 0),
            warn=stats_dict.get("WARN", 0),
            error=stats_dict.get("ERROR", 0),
            debug=stats_dict.get("DEBUG", 0),
            metric=stats_dict.get("METRIC", 0),
        )
        return BaseAPIResponse.success_response(
            data=stats_data,
            message="Log stats retrieved successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving log stats.",
        )


@router.get(
    "/logs/metrics",
    response_model=BaseAPIResponse[PaginatedData[LogMetricSummaryResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_metrics_summary(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(100, ge=1, le=1000, description="Items per page"),
    logger: LoggerService = Depends(get_logger_service),
):
    """Retrieve system metrics summary grouped by source with pagination."""
    try:
        limit = per_page
        offset = (page - 1) * per_page
        raw_metrics, total = await logger.get_metrics_summary(limit=limit, offset=offset)

        items = [LogMetricSummaryResponse(**metric) for metric in raw_metrics]
        paginated_data = PaginatedData[LogMetricSummaryResponse](
            items=items,
            total=total,
            page=page,
            per_page=per_page,
        )
        return BaseAPIResponse.success_response(
            data=paginated_data,
            message="Log metrics summary retrieved successfully.",
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving log metrics summary.",
        )
