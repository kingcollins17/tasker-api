from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

from fastapi import Depends
from geoalchemy2 import Geography
from pydantic import BaseModel, Field
from sqlalchemy import cast, func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.models.services import ProviderServiceLink, Service
from app.core.models.tasks import TaskDispatchAttempt
from app.core.models.users import (
    DutyStatus,
    KYCStatus,
    ProviderProfile,
    User,
    UserLocation,
    UserStats,
)
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
    acceptance_rate_30d: Optional[float] = Field(default=100.0, description="Rolling 30-day percentage of accepted pings")
    average_ratings: Optional[float] = Field(default=0.0, description="Average rating score")
    credibility_score: Optional[float] = Field(default=0.0, description="Credibility score metric")


class GeoService:
    """Postgres / PostGIS spatial implementation using AsyncSession queries (`ST_DWithin`, `ST_Distance`)."""

    def __init__(
        self,
        session: AsyncSession,
    ):
        self.session = session

    async def remove_provider_location(self, provider_id: str) -> bool:
        stmt = select(UserLocation).where(col(UserLocation.user_id) == provider_id)
        result = await self.session.exec(stmt)
        loc: Optional[UserLocation] = result.one_or_none()
        if loc:
            loc.latitude = None
            loc.longitude = None
            loc.last_known_location = None
            loc.updated_at = lagos_now()
            self.session.add(loc)
            return True
        return False

    async def get_provider_location(self, provider_id: str) -> Optional[ProviderLocationPing]:
        stmt = select(UserLocation).where(col(UserLocation.user_id) == provider_id)
        result = await self.session.exec(stmt)

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
        task_id: Optional[str] = None,
        exclude_previous_sessions: bool = True,
        dispatch_session_id: Optional[str] = None,
    ) -> List[NearbyProviderResult]:
        target_point = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)
        distance_m_expr = func.ST_Distance(
            cast(UserLocation.last_known_location, Geography),
            cast(target_point, Geography),
        )

        stmt = (
            select(  # type: ignore
                UserLocation,
                ProviderProfile,
                (distance_m_expr / 1000.0).label("distance_km"),
                col(UserStats.acceptance_rate_30d),
                col(UserStats.average_ratings),
                col(UserStats.credibility_score),
            )
            .join(User, col(User.id) == col(UserLocation.user_id))
            .join(ProviderProfile, col(UserLocation.user_id) == col(ProviderProfile.user_id))
            .outerjoin(UserStats, col(UserStats.user_id) == col(User.id))
        )

        if service_id:
            stmt = (
                stmt.join(
                    ProviderServiceLink,
                    col(ProviderServiceLink.provider_id) == col(ProviderProfile.user_id),  # type: ignore
                )
                .join(
                    Service,
                    col(Service.id) == col(ProviderServiceLink.service_id),
                )
                .where(
                    col(ProviderServiceLink.service_id) == service_id,
                    func.coalesce(col(UserStats.current_tier), 1) >= col(Service.min_tier_required),
                )
            )

        stmt = (
            stmt.where(col(UserLocation.last_known_location) != None)  # noqa: E711
            .where(col(User.is_active) == True)  # noqa: E712
            .where(col(ProviderProfile.kyc_status) == KYCStatus.VERIFIED)
            .where(col(ProviderProfile.is_online) == True)  # noqa: E712
            .where(col(ProviderProfile.duty_status) == DutyStatus.ONLINE_AVAILABLE)
            .where(
                func.ST_DWithin(
                    cast(UserLocation.last_known_location, Geography),
                    cast(target_point, Geography),
                    radius_km * 1000.0,
                )
            )
        )

        if task_id:
            subq = select(1).where(
                col(TaskDispatchAttempt.task_id) == task_id,
                col(TaskDispatchAttempt.provider_id) == col(UserLocation.user_id),
            )
            if not exclude_previous_sessions and dispatch_session_id:
                subq = subq.where(col(TaskDispatchAttempt.dispatch_session_id) == dispatch_session_id)
            stmt = stmt.where(~subq.exists())

        if excluded_provider_ids:
            stmt = stmt.where(~col(UserLocation.user_id).in_(excluded_provider_ids))

        stmt = stmt.order_by(distance_m_expr)

        if limit:
            stmt = stmt.limit(limit)

        result = await self.session.exec(stmt)
        rows = result.all()

        candidates: List[NearbyProviderResult] = []
        for loc, profile, dist_km, acceptance_rate, avg_rating, credibility in rows:
            candidates.append(
                NearbyProviderResult(
                    provider_id=loc.user_id,
                    distance_km=round(float(dist_km), 2) if dist_km is not None else 0.0,
                    latitude=loc.latitude,
                    longitude=loc.longitude,
                    last_heartbeat_at=loc.updated_at.isoformat() if loc.updated_at else None,
                    is_online=profile.is_online if profile.is_online is not None else True,
                    acceptance_rate_30d=acceptance_rate if acceptance_rate is not None else 100.0,
                    average_ratings=avg_rating if avg_rating is not None else 0.0,
                    credibility_score=credibility if credibility is not None else 0.0,
                )
            )

        return candidates


def get_geo_service(
    session: AsyncSession = Depends(get_session),
) -> GeoService:
    """Dependency provider returning the primary GeoService implementation."""
    return GeoService(session=session)


# Aliases for backwards compatibility
PostGISProviderLocationService = GeoService
ProviderLocationService = GeoService
get_provider_location_service = get_geo_service

