from typing import List
from fastapi import APIRouter, Depends, status, Response
from app.core.api_response import BaseAPIResponse
from app.core.repository import GetRepository, Repository
from app.core.models.regions import Region
from app.features.regions.schemas import RegionResponse

router = APIRouter()

get_region_repo = GetRepository(Region)

@router.get("/", response_model=BaseAPIResponse[List[RegionResponse]], status_code=status.HTTP_200_OK)
async def get_regions(
    response: Response,
    region_repo: Repository[Region] = Depends(get_region_repo)
):
    """Retrieve all regions in the database."""
    try:
        regions = await region_repo.get_all()
        data = [RegionResponse.model_validate(r) for r in regions]
        return BaseAPIResponse[List[RegionResponse]](
            data=data,
            detail="Regions retrieved successfully.",
            statusCode=status.HTTP_200_OK
        )
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return BaseAPIResponse[List[RegionResponse]](
            detail=f"An unexpected error occurred: {str(e)}",
            statusCode=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

