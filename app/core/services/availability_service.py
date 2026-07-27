import zoneinfo
from datetime import datetime
from sqlalchemy import ColumnElement, cast, Time
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession
from fastapi import Depends
from sqlalchemy.sql.expression import exists

from app.core.database import get_session
from app.core.repository import Repository, GetRepository
from app.core.models.users import User, UserLocation, ProviderAvailability


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
                ProviderAvailability.day_of_week == func.extract('DOW', local_ts) + 1,
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

    async def update_provider_availability(self, provider_id: str, blocks: list[dict]) -> list[ProviderAvailability]:
        """Replace all availability blocks for a provider."""
        # Delete existing blocks
        delete_stmt = select(ProviderAvailability).where(ProviderAvailability.provider_id == provider_id)
        result = await self.availability_repo.execute(delete_stmt)
        existing = result.all()
        for e in existing:
            await self.availability_repo.delete(e.id)
            
        new_blocks = []
        for b in blocks:
            obj = ProviderAvailability(
                provider_id=provider_id,
                day_of_week=b["day_of_week"],
                start_time=b["start_time"],
                end_time=b["end_time"]
            )
            await self.availability_repo.add(obj)
            new_blocks.append(obj)
            
        return new_blocks


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

