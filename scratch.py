from datetime import datetime
from sqlalchemy import select, func, cast, Integer, Time, String
from app.core.models.users import User, UserLocation, ProviderAvailability
from sqlalchemy.dialects import postgresql

local_ts = func.timezone(UserLocation.timezone, datetime.utcnow())

stmt = select(1).select_from(ProviderAvailability).join(UserLocation, UserLocation.user_id == ProviderAvailability.provider_id).where(
    ProviderAvailability.provider_id == User.id,
    ProviderAvailability.is_active == True,
    cast(ProviderAvailability.day_of_week, Integer) == cast(func.extract('DOW', local_ts), Integer) + 1,
    ProviderAvailability.start_time <= cast(local_ts, Time),
    ProviderAvailability.end_time >= cast(local_ts, Time)
)

print(stmt.compile(dialect=postgresql.dialect()))
