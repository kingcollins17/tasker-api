from app.core.utils.timer import Timer
from app.core.services.logger_service import LoggerService, get_logger_service
from fastapi import APIRouter, Depends, status, UploadFile, File, Form, HTTPException
from app.core.error_handler import AppErrorHandler
from app.core.api_response import BaseAPIResponse
from app.core.deps import GetCurrentUser
from app.core.models.users import UserType, KYCStatus
from app.features.users.schemas import ProviderProfileResponse, UserResponse
from app.features.users.services import UserService, get_user_service
from app.core.services.storage import StorageService, get_storage_service

router = APIRouter()


@router.post(
    "/kyc/selfie",
    response_model=BaseAPIResponse[ProviderProfileResponse],
    status_code=status.HTTP_200_OK,
)
async def submit_kyc_selfie(
    selfie: UploadFile = File(..., description="Selfie for liveness verification"),
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_type=UserType.PROVIDER,
            required_active=True,
            required_email_verified=True,
            required_phone_verified=True,
        )
    ),
    user_service: UserService = Depends(get_user_service),
    storage_service: StorageService = Depends(get_storage_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Submit KYC selfie for liveness verification."""
    try:
        timer = Timer()
        timer.start()
        selfie_url = await storage_service.upload_file(selfie)

        profile = await user_service.submit_kyc_selfie(
            user_id=current_user.id, selfie_url=selfie_url
        )

        await system_logger.metric('submit_kyc_selfie', timer.stop(), source='kyc.submit_kyc_selfie')
        return BaseAPIResponse[ProviderProfileResponse](
            data=ProviderProfileResponse.model_validate(profile),
            detail="KYC selfie submitted successfully.",
            statusCode=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('submit_kyc_selfie failed', source='kyc.submit_kyc_selfie', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'submit_kyc_selfie error: {str(e)}', source='kyc.submit_kyc_selfie')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during KYC selfie submission.",
        )


@router.post(
    "/kyc/document",
    response_model=BaseAPIResponse[ProviderProfileResponse],
    status_code=status.HTTP_200_OK,
)
async def submit_kyc_document(
    id_type: str = Form(..., description="Type of ID card (e.g., NIN, BVN)"),
    id_number: str = Form(..., description="ID card number"),
    id_doc: UploadFile = File(..., description="ID document image/PDF"),
    current_user: UserResponse = Depends(
        GetCurrentUser(
            required_type=UserType.PROVIDER,
            required_active=True,
            required_email_verified=True,
            required_phone_verified=True,
            allowed_kyc_statuses=[KYCStatus.PENDING_SUBMISSION, KYCStatus.FAILED],
        )
    ),
    user_service: UserService = Depends(get_user_service),
    storage_service: StorageService = Depends(get_storage_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Submit KYC verification document details."""
    try:
        timer = Timer()
        timer.start()
        id_doc_url = await storage_service.upload_file(id_doc)

        profile = await user_service.submit_kyc_document(
            user_id=current_user.id,
            id_type=id_type,
            id_number=id_number,
            id_doc_url=id_doc_url,
        )

        await system_logger.metric('submit_kyc_document', timer.stop(), source='kyc.submit_kyc_document')
        return BaseAPIResponse[ProviderProfileResponse](
            data=ProviderProfileResponse.model_validate(profile),
            detail="KYC document submitted successfully.",
            statusCode=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('submit_kyc_document failed', source='kyc.submit_kyc_document', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'submit_kyc_document error: {str(e)}', source='kyc.submit_kyc_document')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during KYC document submission.",
        )


@router.get(
    "/kyc",
    response_model=BaseAPIResponse[ProviderProfileResponse],
    status_code=status.HTTP_200_OK,
)
async def get_kyc_status(
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)
    ),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Retrieve the KYC status and submission details of the current provider."""
    try:
        timer = Timer()
        timer.start()
        if not current_user.provider_profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found.",
            )
        await system_logger.metric('get_kyc_status', timer.stop(), source='kyc.get_kyc_status')
        return BaseAPIResponse[ProviderProfileResponse](
            data=current_user.provider_profile,
            detail="KYC details retrieved successfully.",
            statusCode=status.HTTP_200_OK,
        )
    except HTTPException as e:
        await system_logger.warn('get_kyc_status failed', source='kyc.get_kyc_status', metadata={'detail': str(e.detail) if hasattr(e, 'detail') else str(e)})
        raise e
    except Exception as e:
        await system_logger.error(f'get_kyc_status error: {str(e)}', source='kyc.get_kyc_status')
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during KYC status retrieval.",
        )
