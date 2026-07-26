from enum import Enum
from uuid import uuid4
from datetime import datetime
from typing import Optional, Dict, Any
from sqlmodel import Field, SQLModel, JSON
from sqlalchemy import Column
from app.core.utils.datetime_helper import utc_now

class LogLevel(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    DEBUG = "DEBUG"
    METRIC = "METRIC"

class SystemLog(SQLModel, table=True):
    __tablename__ = "system_logs"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    level: LogLevel = Field(default=LogLevel.INFO, index=True)
    message: str = Field(...)
    source: Optional[str] = Field(default=None, index=True)
    duration_ms: Optional[int] = Field(default=None)
    metadata_: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=Column("metadata", JSON, nullable=True)
    )
    created_at: datetime = Field(default_factory=utc_now, index=True)

