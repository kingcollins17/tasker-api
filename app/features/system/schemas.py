from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict

from app.core.models.system_logs import LogLevel


class SystemLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    level: LogLevel
    message: str
    source: Optional[str] = None
    duration_ms: Optional[int] = None
    metadata_: Optional[Dict[str, Any]] = None
    created_at: datetime


class LogStatsResponse(BaseModel):
    info: int = 0
    warn: int = 0
    error: int = 0
    debug: int = 0
    metric: int = 0


class LogMetricSummaryResponse(BaseModel):
    source: Optional[str] = None
    count: int = 0
    avg_duration: float = 0.0
    max_duration: Optional[int] = None
    min_duration: Optional[int] = None
