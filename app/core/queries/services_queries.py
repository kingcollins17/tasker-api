from sqlmodel import select, func, desc
from sqlalchemy.orm import selectinload
from app.core.models.services import Service, ProviderServiceLink
from app.core.models.users import User

class ServicesQueries:
    @staticmethod
    def get_available_services_in_region_query(region_id: str):
        statement = (
            select(Service)
            .join(ProviderServiceLink, Service.id == ProviderServiceLink.service_id)
            .join(User, ProviderServiceLink.provider_id == User.id)
            .where(User.region_id == region_id)
            .where(User.is_active == True)
            .where(Service.is_active == True)
            .distinct()
            .options(selectinload(Service.category))  # type: ignore
        )
        
        count_statement = (
            select(func.count(Service.id.distinct()))
            .select_from(Service)
            .join(ProviderServiceLink, Service.id == ProviderServiceLink.service_id)
            .join(User, ProviderServiceLink.provider_id == User.id)
            .where(User.region_id == region_id)
            .where(User.is_active == True)
            .where(Service.is_active == True)
        )
        
        return statement, count_statement

    @staticmethod
    def check_service_availability_in_region_query(service_id: str, region_id: str):
        statement = (
            select(func.count(Service.id))
            .select_from(Service)
            .join(ProviderServiceLink, Service.id == ProviderServiceLink.service_id)
            .join(User, ProviderServiceLink.provider_id == User.id)
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
            .join(ProviderServiceLink, User.id == ProviderServiceLink.provider_id)
            .join(Service, ProviderServiceLink.service_id == Service.id)
            .where(Service.id == service_id)
            .where(User.region_id == region_id)
            .where(User.is_active == True)
            .where(Service.is_active == True)
            .order_by(desc(User.average_ratings), desc(User.credibility_score))
        )
        
        count_statement = (
            select(func.count(User.id))
            .select_from(User)
            .join(ProviderServiceLink, User.id == ProviderServiceLink.provider_id)
            .join(Service, ProviderServiceLink.service_id == Service.id)
            .where(Service.id == service_id)
            .where(User.region_id == region_id)
            .where(User.is_active == True)
            .where(Service.is_active == True)
        )
        
        return statement, count_statement
