from fastapi import APIRouter, Depends, Query, HTTPException, status
from typing import Optional, List
from sqlalchemy import cast, func, or_
from geoalchemy2 import Geography
from sqlmodel import select

from app.core.repository import Repository, GetRepository, QueryOptions
from sqlalchemy.orm import noload
from app.core.models.users import ProviderProfile, User, UserStats, UserLocation, DutyStatus, KYCStatus
from app.core.schemas.users import MinimalProviderResponse, UserLocationResponse
from app.features.users.schemas import PublicProviderProfileResponse, PublicUserResponse
from app.core.error_handler import AppErrorHandler
from app.core.api_response import BaseAPIResponse, PaginatedData

router = APIRouter(prefix="/providers", tags=["Providers"])

@router.get("/{provider_id}", response_model=BaseAPIResponse[PublicUserResponse], status_code=status.HTTP_200_OK)
async def get_public_provider_profile(
    provider_id: str,
    user_repo: Repository[User] = Depends(GetRepository(User)),
):
    try:
        user = await user_repo.get(provider_id)
        if not user or not user.provider_profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider profile not found")

        profile = user.provider_profile

        if user.location:
            loc_data = UserLocationResponse.model_validate(user.location)
        else:
            loc_data = None

        provider_profile_data = PublicProviderProfileResponse.model_validate(profile)

        stats = user.stats
        credibility_score = stats.credibility_score if stats else 25.0
        average_ratings = stats.average_ratings if stats else 0.0

        user_data = PublicUserResponse(
            id=user.id,
            email=user.email,
            phone_number=user.phone_number,
            type=user.type,
            is_active=user.is_active,
            email_verified=user.email_verified,
            phone_verified=user.phone_verified,
            credibility_score=credibility_score,
            average_ratings=average_ratings,
            created_at=user.created_at,
            region_id=user.region_id,
            location=loc_data,
            services=profile.services,
            profile=provider_profile_data
        )

        return BaseAPIResponse[PublicUserResponse](
            data=user_data,
            detail="Provider profile retrieved successfully.",
            status_code=status.HTTP_200_OK
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve provider profile."
        )

