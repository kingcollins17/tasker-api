from fastapi import APIRouter, Depends, Query, HTTPException, status
from typing import Optional, List
from sqlalchemy import cast, func, or_
from geoalchemy2 import Geography
from sqlmodel import select

from app.core.repository import Repository, GetRepository, QueryOptions
from sqlalchemy.orm import noload
from app.core.models.users import ProviderProfile, User, UserLocation, DutyStatus, KYCStatus, ProviderAvailability
from app.core.schemas.users import MinimalProviderResponse, UserLocationResponse
from app.features.users.schemas import PublicProviderProfileResponse, PublicUserResponse, ProviderAvailabilityResponse
from app.core.error_handler import AppErrorHandler
from app.core.api_response import BaseAPIResponse, PaginatedData

router = APIRouter(prefix="/providers", tags=["Providers"])

@router.get("/{provider_id}", response_model=BaseAPIResponse[PublicUserResponse], status_code=status.HTTP_200_OK)
async def get_public_provider_profile(
    provider_id: str,
    user_repo: Repository[User] = Depends(GetRepository(User)),
    availability_repo: Repository[ProviderAvailability] = Depends(GetRepository(ProviderAvailability))
):
    try:
        user = await user_repo.get(provider_id)
        if not user or not user.provider_profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider profile not found")

        profile = user.provider_profile

        if user.location:
            loc_data = UserLocationResponse.model_validate(user.location)
        else:
            loc_data = UserLocationResponse(
                user_id=user.id,
                region_id=user.region_id
            )
        
        availabilities = await availability_repo.get_all(QueryOptions(filters={"provider_id": provider_id}))
        availability_data = [ProviderAvailabilityResponse.model_validate(a) for a in availabilities]

        provider_profile_data = PublicProviderProfileResponse.model_validate(profile)

        user_data = PublicUserResponse(
            id=user.id,
            email=user.email,
            phone_number=user.phone_number,
            type=user.type,
            is_active=user.is_active,
            email_verified=user.email_verified,
            phone_verified=user.phone_verified,
            credibility_score=user.credibility_score,
            average_ratings=user.average_ratings,
            created_at=user.created_at,
            region_id=user.region_id,
            location=loc_data,
            services=profile.services,
            availability=availability_data,
            profile=provider_profile_data
        )

        return BaseAPIResponse[PublicUserResponse](
            data=user_data,
            detail="Provider profile retrieved successfully.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(status_code=500, detail="Failed to fetch provider profile")


@router.get("", response_model=BaseAPIResponse[PaginatedData[MinimalProviderResponse]], status_code=status.HTTP_200_OK)
async def list_providers(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    latitude: Optional[float] = Query(None, description="Latitude for proximity search"),
    longitude: Optional[float] = Query(None, description="Longitude for proximity search"),
    radius_km: Optional[float] = Query(10.0, description="Radius in kilometers for proximity search"),
    search: Optional[str] = Query(None, description="Search by first name, last name, or email"),
    is_online: Optional[bool] = Query(None, description="Filter by online status"),
    duty_status: Optional[DutyStatus] = Query(None, description="Filter by duty status"),
    kyc_status: Optional[KYCStatus] = Query(None, description="Filter by KYC status"),
    provider_repo: Repository[ProviderProfile] = Depends(GetRepository(ProviderProfile)),
):
    try:
        statement = select(
            ProviderProfile,
            User.email,
            User.phone_number,
            User.average_ratings,
            User.credibility_score,
            UserLocation.latitude,
            UserLocation.longitude,
            UserLocation.address_line
        ).join(
            User, ProviderProfile.user_id == User.id
        ).outerjoin(
            UserLocation, User.id == UserLocation.user_id
        )

        count_statement = select(func.count(ProviderProfile.id)).join(
            User, ProviderProfile.user_id == User.id
        ).outerjoin(
            UserLocation, User.id == UserLocation.user_id
        )

        if is_online is not None:
            statement = statement.where(ProviderProfile.is_online == is_online)
            count_statement = count_statement.where(ProviderProfile.is_online == is_online)
        
        if duty_status is not None:
            statement = statement.where(ProviderProfile.duty_status == duty_status)
            count_statement = count_statement.where(ProviderProfile.duty_status == duty_status)
            
        if kyc_status is not None:
            statement = statement.where(ProviderProfile.status == kyc_status)
            count_statement = count_statement.where(ProviderProfile.status == kyc_status)
            
        if search:
            search_term = f"%{search}%"
            search_filter = or_(
                ProviderProfile.first_name.ilike(search_term),
                ProviderProfile.last_name.ilike(search_term),
                User.email.ilike(search_term),
            )
            statement = statement.where(search_filter)
            count_statement = count_statement.where(search_filter)

        if latitude is not None and longitude is not None:
            target_point = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)
            spatial_filter = func.ST_DWithin(
                cast(UserLocation.last_known_location, Geography),
                cast(target_point, Geography),
                radius_km * 1000.0,
            )
            statement = statement.where(spatial_filter)
            count_statement = count_statement.where(spatial_filter)

        total_result = await provider_repo.execute(count_statement)
        total = total_result.first() or 0

        statement = statement.offset((page - 1) * per_page).limit(per_page)

        results = await provider_repo.execute(statement)
        rows = results.all()

        items = []
        for profile, email, phone_number, average_ratings, credibility_score, lat, lng, addr in rows:
            loc_data = None
            if lat is not None or lng is not None or addr is not None:
                loc_data = UserLocationResponse(
                    latitude=lat,
                    longitude=lng,
                    address_line=addr
                )
            
            fullname = None
            first_name = profile.first_name or ""
            last_name = profile.last_name or ""
            fullname = f"{first_name} {last_name}".strip() or None
            
            provider_data = MinimalProviderResponse(
                id=profile.user_id,
                fullname=fullname,
                email=email,
                phone_number=phone_number,
                average_ratings=average_ratings,
                credibility_score=credibility_score,
                gender=profile.gender,
                profile_picture_url=profile.selfie_url,
                selfie_url=profile.selfie_url,
                total_tasks_completed=profile.total_tasks_completed,
                location=loc_data
            )
            items.append(provider_data)

        data = PaginatedData[MinimalProviderResponse](
            items=items,
            total=total,
            page=page,
            per_page=per_page
        )
        
        return BaseAPIResponse[PaginatedData[MinimalProviderResponse]](
            data=data,
            detail="Providers retrieved successfully.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(status_code=500, detail="Failed to fetch providers")
