import abc
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import cast, func
from geoalchemy2 import Geography
from sqlmodel import select, col

from app.core.database import get_session
from sqlmodel.ext.asyncio.session import AsyncSession
from app.core.models.services import ProviderServiceLink
from app.core.models.users import ProviderProfile, UserLocation
from app.core.repository import Repository
from app.core.services.cache import CacheService, get_cache_service
from app.core.utils.datetime_helper import lagos_now


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
        limit: Optional[int] = 100,
        excluded_provider_ids: Optional[List[str]] = None,
        service_id: Optional[str] = None,
    ) -> List[NearbyProviderResult]:
        """Queries spatial index to find candidate providers within radius_km sorted by distance."""
        pass





class PostGISProviderLocationService(ProviderLocationService):
    """Postgres / PostGIS spatial implementation using Repository queries (`ST_DWithin`, `ST_Distance`)."""

    def __init__(
        self,
        location_repo: Repository[UserLocation],
        provider_profile_repo: Repository[ProviderProfile],
    ):
        self.location_repo = location_repo
        self.provider_profile_repo = provider_profile_repo



    async def remove_provider_location(self, provider_id: str) -> bool:
        stmt = select(UserLocation).where(UserLocation.user_id == provider_id)
        result = await self.location_repo.execute(stmt)
        loc: Optional[UserLocation] = result.one_or_none()
        if loc:
            loc.latitude = None
            loc.longitude = None
            loc.last_known_location = None
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
        excluded_provider_ids: Optional[List[str]] = None,
        service_id: Optional[str] = None,
    ) -> List[NearbyProviderResult]:
        target_point = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)
        distance_m_expr = func.ST_Distance(
            cast(UserLocation.last_known_location, Geography),
            cast(target_point, Geography),
        )

        stmt = (
            select(
                UserLocation,
                ProviderProfile,
                (distance_m_expr / 1000.0).label("distance_km"),
            )
            .join(ProviderProfile, col(UserLocation.user_id) == col(ProviderProfile.user_id))
        )

        if service_id:
            stmt = stmt.join(
                ProviderServiceLink,
                ProviderServiceLink.provider_id == ProviderProfile.user_id,  # type: ignore
            ).where(
                ProviderServiceLink.service_id == service_id,
            )

        stmt = (
            stmt.where(UserLocation.last_known_location != None)  # noqa: E711
            .where(ProviderProfile.is_online == True)  # noqa: E712
            .where(
                func.ST_DWithin(
                    cast(UserLocation.last_known_location, Geography),
                    cast(target_point, Geography),
                    radius_km * 1000.0,
                )
            )
            .order_by(distance_m_expr)
        )

        if limit:
            stmt = stmt.limit(limit)
        if excluded_provider_ids:
            stmt = stmt.where(~col(UserLocation.user_id).in_(excluded_provider_ids))

        result = await self.location_repo.execute(stmt)
        rows = result.all()

        candidates: List[NearbyProviderResult] = []
        for loc, profile, dist_km in rows:
            candidates.append(
                NearbyProviderResult(
                    provider_id=loc.user_id,
                    distance_km=round(float(dist_km), 2) if dist_km is not None else 0.0,
                    latitude=loc.latitude,
                    longitude=loc.longitude,
                    last_heartbeat_at=loc.updated_at.isoformat() if loc.updated_at else None,
                    is_online=profile.is_online if profile.is_online is not None else True,
                )
            )

        return candidates


def get_provider_location_service(
    session: AsyncSession = Depends(get_session),
) -> ProviderLocationService:
    """Dependency provider returning the primary PostGISProviderLocationService implementation."""
    return PostGISProviderLocationService(
        location_repo=Repository(UserLocation, session),
        provider_profile_repo=Repository(ProviderProfile, session),
    )
