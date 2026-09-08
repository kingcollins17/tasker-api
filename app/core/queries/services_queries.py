from typing import List
from sqlmodel import select, func, desc, col
from sqlalchemy.orm import selectinload
from sqlalchemy import cast
from geoalchemy2 import Geography
from app.core.models.services import Service, ProviderServiceLink
from app.core.models.users import User, UserLocation

class ServicesQueries:
    @staticmethod
    def get_available_services_in_region_query(region_id: str):
        statement = (
            select(Service)
            .join(ProviderServiceLink, col(Service.id) == ProviderServiceLink.service_id)
            .join(User, col(ProviderServiceLink.provider_id) == User.id)
            .where(User.region_id == region_id)
            .where(User.is_active == True)
            .where(Service.is_active == True)
            .distinct()
            .options(selectinload(Service.category))  # type: ignore
        )
        
        count_statement = (
            select(func.count(col(Service.id).distinct()))
            .select_from(Service)
            .join(ProviderServiceLink, col(Service.id) == ProviderServiceLink.service_id)
            .join(User, col(ProviderServiceLink.provider_id) == User.id)
            .where(User.region_id == region_id)
            .where(User.is_active == True)
            .where(Service.is_active == True)
        )
        
        return statement, count_statement

    @staticmethod
    def check_service_availability_in_region_query(service_id: str, region_id: str):
        statement = (
            select(func.count(col(Service.id)))
            .select_from(Service)
            .join(ProviderServiceLink, col(Service.id) == ProviderServiceLink.service_id)
            .join(User, col(ProviderServiceLink.provider_id) == User.id)
            .where(Service.id == service_id)
            .where(User.region_id == region_id)
            .where(User.is_active == True)
            .where(Service.is_active == True)
        )
        return statement

    @staticmethod
    def get_providers_for_service_in_region_query(service_id: str, region_id: str):
        statement = (
            select(User)
            .join(ProviderServiceLink, col(User.id) == ProviderServiceLink.provider_id)
            .join(Service, col(ProviderServiceLink.service_id) == Service.id)
            .where(Service.id == service_id)
            .where(User.region_id == region_id)
            .where(User.is_active == True)
            .where(Service.is_active == True)
            .order_by(desc(User.average_ratings), desc(User.credibility_score))
        )
        
        count_statement = (
            select(func.count(col(User.id)))
            .select_from(User)
            .join(ProviderServiceLink, col(User.id) == ProviderServiceLink.provider_id)
            .join(Service, col(ProviderServiceLink.service_id) == Service.id)
            .where(Service.id == service_id)
            .where(User.region_id == region_id)
            .where(User.is_active == True)
            .where(Service.is_active == True)
        )
        
        return statement, count_statement

    @staticmethod
    def bulk_check_services_availability_query(
        service_ids: List[str],
        region_id: str,
        latitude: float,
        longitude: float,
        radius_km: float,
    ):
        """
        For each service_id, count active providers in the given region
        whose last_known_location is within radius_km of (latitude, longitude).
        """
        target_point = func.ST_SetSRID(
            func.ST_MakePoint(longitude, latitude), 4326
        )
        spatial_filter = func.ST_DWithin(
            cast(UserLocation.last_known_location, Geography),
            cast(target_point, Geography),
            radius_km * 1000.0,
        )

        statement = (
            select(
                col(Service.id).label("service_id"),
                col(Service.name).label("service_name"),
                func.count(col(User.id).distinct()).label("provider_count"),
            )
            .select_from(Service)
            .join(ProviderServiceLink, col(Service.id) == ProviderServiceLink.service_id)
            .join(User, col(ProviderServiceLink.provider_id) == User.id)
            .join(UserLocation, col(User.id) == UserLocation.user_id)
            .where(col(Service.id).in_(service_ids))
            .where(UserLocation.region_id == region_id)
            .where(User.is_active == True)
            .where(Service.is_active == True)
            .where(spatial_filter)
            .group_by(Service.id, Service.name)
        )

        return statement
