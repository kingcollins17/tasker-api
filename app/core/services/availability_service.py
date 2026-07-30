from typing import Optional
import zoneinfo
from datetime import datetime, time
from sqlalchemy import ColumnElement, cast, Time, String
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession
from fastapi import Depends, HTTPException, status
from sqlalchemy.sql.expression import exists

from app.core.database import get_session
from app.core.repository import Repository, GetRepository
from app.core.models.users import User, UserLocation, ProviderAvailability, DayOfWeek


class AvailabilityService:
    def __init__(self, availability_repo: Repository[ProviderAvailability], user_location_repo: Repository[UserLocation]):
        self.availability_repo = availability_repo
        self.user_location_repo = user_location_repo

    def get_availability_sql_condition(self, target_utc: datetime) -> ColumnElement[bool]:
        """
        Returns a SQLAlchemy EXISTS expression that filters for providers 
        whose configured availability blocks cover the local equivalent of target_utc.
        """
        # PostgreSQL timezone function usage: timezone(zone, timestamp)
        # EXTRACT(DOW) returns 0=Sunday, 6=Saturday. We add 1 to match DayOfWeek Enum (1=Sunday, 7=Saturday).
        local_ts = func.timezone(UserLocation.timezone, target_utc)
        
        return exists(
            select(1)
            .select_from(ProviderAvailability)
            # pyrefly: ignore [bad-argument-type]
            .join(UserLocation, UserLocation.user_id == ProviderAvailability.provider_id)
            .where(
                ProviderAvailability.provider_id == User.id,
                ProviderAvailability.is_active == True,
                cast(ProviderAvailability.day_of_week, String) == func.upper(func.to_char(local_ts, 'FMDay')),
                ProviderAvailability.start_time <= cast(local_ts, Time),
                ProviderAvailability.end_time >= cast(local_ts, Time)
            )
        )

    async def is_provider_available(self, provider_id: str, target_utc: datetime) -> bool:
        """
        Programmatically checks if a specific provider is available at the target UTC time.
        """
        # Fetch the user's timezone
        loc_stmt = select(UserLocation).where(UserLocation.user_id == provider_id)
        res = await self.user_location_repo.execute(loc_stmt)
        res_all = res.all()
        tz_str = res_all[0].timezone if res_all else "UTC"
        
        tz = zoneinfo.ZoneInfo(tz_str)
        local_dt = target_utc.astimezone(tz)
        
        # Map python weekday (0=Mon, 6=Sun) to DayOfWeek (1=Sun, 2=Mon... 7=Sat)
        day_val = (local_dt.weekday() + 1) % 7 + 1
        target_time = local_dt.time()
        
        stmt = select(ProviderAvailability).where(
            ProviderAvailability.provider_id == provider_id,
            ProviderAvailability.is_active == True,
            ProviderAvailability.day_of_week == day_val,
            ProviderAvailability.start_time <= target_time,
            ProviderAvailability.end_time >= target_time
        )
        result = await self.availability_repo.execute(stmt)
        return len(result.all()) > 0

    async def get_provider_availability(self, provider_id: str) -> list[ProviderAvailability]:
        """Fetch all availability blocks for a provider."""
        stmt = select(ProviderAvailability).where(ProviderAvailability.provider_id == provider_id)
        result = await self.availability_repo.execute(stmt)
        return list(result.all())

    async def update_availability_block(
        self,
        availability_id: str,
        provider_id: str,
        start_time: Optional[time] = None,
        end_time: Optional[time] = None,
        is_active: Optional[bool] = None,
    ) -> ProviderAvailability:
        """Update a specific availability block for a provider (start_time, end_time, is_active)."""
        block = await self.availability_repo.get(availability_id)
        if not block or block.provider_id != provider_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Availability block not found.",
            )
        if start_time is not None:
            block.start_time = start_time
        if end_time is not None:
            block.end_time = end_time
        if is_active is not None:
            block.is_active = is_active
        return await self.availability_repo.add(block)

    async def create_default_availability(self, provider_id: str) -> list[ProviderAvailability]:
        """Create default availability for all 7 days of the week (06:00:00 to 23:59:00, active) if none exist."""
        existing = await self.get_provider_availability(provider_id)
        if existing:
            return existing

        created = []
        for day in DayOfWeek:
            block = ProviderAvailability(
                provider_id=provider_id,
                day_of_week=day,
                start_time=time(6, 0, 0),
                end_time=time(23, 59, 0),
                is_active=True,
            )
            await self.availability_repo.add(block)
            created.append(block)
        return created


def get_availability_service(
    availability_repo: Repository[ProviderAvailability] = Depends(GetRepository(ProviderAvailability)),
    user_location_repo: Repository[UserLocation] = Depends(GetRepository(UserLocation))
) -> AvailabilityService:
    return AvailabilityService(availability_repo, user_location_repo)

def get_availability_service_manual(session: AsyncSession) -> AvailabilityService:
    return AvailabilityService(
        Repository(ProviderAvailability, session),
        Repository(UserLocation, session)
    )

