import abc
import math
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import select

from app.core.database import get_session
from sqlmodel.ext.asyncio.session import AsyncSession
from app.core.models.users import ProviderProfile, UserLocation
from app.core.repository import Repository
from app.core.services.cache import CacheService, get_cache_service
from app.core.utils.datetime_helper import lagos_now
from app.core.utils.geo import calculate_haversine_distance


class LocationPoint(BaseModel):
    """Represents a geographical coordinate point."""
    latitude: Optional[float] = Field(default=None, description="WGS84 Latitude coordinate")
    longitude: Optional[float] = Field(default=None, description="WGS84 Longitude coordinate")
    address_line: Optional[str] = Field(default=None, description="Formatted street address string")


class ProviderLocationPing(BaseModel):
    """Real-time provider location heartbeat ping payload."""
    provider_id: Optional[str] = Field(default=None, description="Unique provider user ID")
    latitude: Optional[float] = Field(default=None, description="WGS84 Latitude coordinate")
    longitude: Optional[float] = Field(default=None, description="WGS84 Longitude coordinate")
    heading: Optional[float] = Field(default=None, description="Compass heading direction in degrees (0-360)")
    speed: Optional[float] = Field(default=None, description="Movement speed in meters per second")
    is_online: Optional[bool] = Field(default=True, description="Whether provider is online and available for dispatch")
    timestamp: Optional[str] = Field(default=None, description="ISO format timestamp of location ping")


class NearbyProviderResult(BaseModel):
    """Candidate provider search result within a geographical search radius."""
    provider_id: Optional[str] = Field(default=None, description="Unique provider user ID")
    distance_km: Optional[float] = Field(default=None, description="Calculated straight-line distance in kilometers")
    latitude: Optional[float] = Field(default=None, description="Provider current latitude")
    longitude: Optional[float] = Field(default=None, description="Provider current longitude")
    last_heartbeat_at: Optional[str] = Field(default=None, description="ISO timestamp of last location ping")
    is_online: Optional[bool] = Field(default=True, description="Online status flag")


class ProviderLocationService(abc.ABC):
    """Abstract base class defining the spatial location tracking and candidate search interface."""

    @abc.abstractmethod
    async def update_provider_location(self, ping: ProviderLocationPing, ttl_seconds: int = 300) -> bool:
        """Ingests a real-time provider location ping and updates spatial state."""
        pass

    @abc.abstractmethod
    async def remove_provider_location(self, provider_id: str) -> bool:
        """Removes a provider from the active spatial index when going offline."""
        pass

    @abc.abstractmethod
    async def get_provider_location(self, provider_id: str) -> Optional[ProviderLocationPing]:
        """Fetches current location coordinates and metadata for a provider."""
        pass

    @abc.abstractmethod
    async def search_nearby_providers(
        self,
        latitude: float,
        longitude: float,
        radius_km: float = 10.0,
        limit: Optional[int] = 50,
    ) -> List[NearbyProviderResult]:
        """Queries spatial index to find candidate providers within radius_km sorted by distance."""
        pass

    def calculate_haversine_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Computes great-circle distance in kilometers between coordinate pairs using Haversine formula."""
        R = 6371.0  # Earth radius in kilometers

        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)

        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return round(R * c, 2)





class PostGISProviderLocationService(ProviderLocationService):
    """Postgres / PostGIS spatial implementation using Repository queries (`ST_DWithin`, `ST_Distance`)."""

    def __init__(
        self,
        location_repo: Repository[UserLocation],
        provider_profile_repo: Repository[ProviderProfile],
    ):
        self.location_repo = location_repo
        self.provider_profile_repo = provider_profile_repo

    async def update_provider_location(self, ping: ProviderLocationPing, ttl_seconds: int = 300) -> bool:
        if not ping.provider_id or ping.latitude is None or ping.longitude is None:
            return False

        # Query existing user location via repository
        stmt = select(UserLocation).where(UserLocation.user_id == ping.provider_id)
        result = await self.location_repo.execute(stmt)
        loc: Optional[UserLocation] = result.one_or_none()

        now = lagos_now()
        if loc:
            loc.latitude = ping.latitude
            loc.longitude = ping.longitude
            loc.updated_at = now
            await self.location_repo.add(loc)
        else:
            new_loc = UserLocation(
                user_id=ping.provider_id,
                latitude=ping.latitude,
                longitude=ping.longitude,
                created_at=now,
                updated_at=now,
            )
            await self.location_repo.add(new_loc)

        return True

    async def remove_provider_location(self, provider_id: str) -> bool:
        stmt = select(UserLocation).where(UserLocation.user_id == provider_id)
        result = await self.location_repo.execute(stmt)
        loc: Optional[UserLocation] = result.one_or_none()
        if loc:
            loc.latitude = None
            loc.longitude = None
            loc.updated_at = lagos_now()
            await self.location_repo.add(loc)
            return True
        return False

    async def get_provider_location(self, provider_id: str) -> Optional[ProviderLocationPing]:
        stmt = select(UserLocation).where(UserLocation.user_id == provider_id)
        result = await self.location_repo.execute(stmt)
        loc: Optional[UserLocation] = result.one_or_none()
        if loc and loc.latitude is not None and loc.longitude is not None:
            return ProviderLocationPing(
                provider_id=provider_id,
                latitude=loc.latitude,
                longitude=loc.longitude,
                timestamp=loc.updated_at.isoformat() if loc.updated_at else lagos_now().isoformat(),
            )
        return None

    async def search_nearby_providers(
        self,
        latitude: float,
        longitude: float,
        radius_km: float = 10.0,
        limit: Optional[int] = 100,
    ) -> List[NearbyProviderResult]:
        stmt = (
            select(UserLocation, ProviderProfile)
            # pyrefly: ignore [bad-argument-type]
            .join(ProviderProfile, UserLocation.user_id == ProviderProfile.user_id)
            .where(UserLocation.latitude != None, UserLocation.longitude != None) # noqa: E711
            .where(ProviderProfile.is_online == True) # noqa: E712
        )

        result = await self.location_repo.execute(stmt)
        rows = result.unique().all()

        candidates: List[NearbyProviderResult] = []
        for loc, profile in rows:
            if loc.latitude is not None and loc.longitude is not None:
                dist = calculate_haversine_distance(latitude, longitude, loc.latitude, loc.longitude)
                if dist <= radius_km:
                    candidates.append(
                        NearbyProviderResult(
                            provider_id=loc.user_id,
                            distance_km=dist,
                            latitude=loc.latitude,
                            longitude=loc.longitude,
                            last_heartbeat_at=loc.updated_at.isoformat() if loc.updated_at else None,
                            is_online=profile.is_online if profile.is_online is not None else True,
                        )
                    )

        # Sort by distance ascending
        candidates.sort(key=lambda c: c.distance_km or 0.0)
        if limit:
            candidates = candidates[:limit]
        return candidates


def get_provider_location_service(
    session: AsyncSession = Depends(get_session),
) -> ProviderLocationService:
    """Dependency provider returning the primary PostGISProviderLocationService implementation."""
    return PostGISProviderLocationService(
        location_repo=Repository(UserLocation, session),
        provider_profile_repo=Repository(ProviderProfile, session),
    )
