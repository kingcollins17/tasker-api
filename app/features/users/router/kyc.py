from typing import Optional
from app.core.utils.timer import Timer
from app.core.services.logger_service import LoggerService, get_logger_service
from fastapi import APIRouter, Depends, status, UploadFile, File, Form, HTTPException
from app.core.error_handler import AppErrorHandler
from app.core.api_response import BaseAPIResponse
from app.core.deps import GetCurrentUser
from app.core.models.users import UserType, KYCStatus, KYCDocument
from app.core.repository import Repository, GetRepository, QueryOptions
from app.features.users.schemas import KYCDocumentResponse, ProviderProfileResponse, UserResponse
from app.features.users.services import ProviderProfileService, get_provider_profile_service
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
    provider_service: ProviderProfileService = Depends(get_provider_profile_service),
    storage_service: StorageService = Depends(get_storage_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Submit KYC selfie for liveness verification."""
    try:
        timer = Timer()
        timer.start()
        selfie_url = await storage_service.upload_file(selfie)

        profile = await provider_service.submit_kyc_selfie(
            user_id=current_user.id, selfie_url=selfie_url
        )

        await system_logger.metric('submit_kyc_selfie', timer.stop(), source='kyc.submit_kyc_selfie')
        return BaseAPIResponse[ProviderProfileResponse](
            data=ProviderProfileResponse.model_validate(profile),
            detail="KYC selfie submitted successfully.",
            status_code=status.HTTP_200_OK,
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
    provider_service: ProviderProfileService = Depends(get_provider_profile_service),
    storage_service: StorageService = Depends(get_storage_service),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Submit KYC verification document details."""
    try:
        timer = Timer()
        timer.start()
        id_doc_url = await storage_service.upload_file(id_doc)

        profile = await provider_service.submit_kyc_document(
            user_id=current_user.id,
            id_type=id_type,
            id_number=id_number,
            id_doc_url=id_doc_url,
        )

        await system_logger.metric('submit_kyc_document', timer.stop(), source='kyc.submit_kyc_document')
        return BaseAPIResponse[ProviderProfileResponse](
            data=ProviderProfileResponse.model_validate(profile),
            detail="KYC document submitted successfully.",
            status_code=status.HTTP_200_OK,
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
    response_model=BaseAPIResponse[Optional[KYCDocumentResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_kyc_status(
    current_user: UserResponse = Depends(
        GetCurrentUser(required_type=UserType.PROVIDER)
    ),
    kyc_repo: Repository[KYCDocument] = Depends(GetRepository(KYCDocument)),
    system_logger: LoggerService = Depends(get_logger_service)
):
    """Retrieve the most recent KYC document submission of the authenticated provider."""
    try:
        timer = Timer()
        timer.start()
        docs = await kyc_repo.get_all(
            QueryOptions(filters={"user_id": current_user.id}, order_by="attempt_number", descending=True, limit=1)
        )
        latest_kyc = docs[0] if docs else None
        doc_response = KYCDocumentResponse.model_validate(latest_kyc) if latest_kyc else None

        await system_logger.metric('get_kyc_status', timer.stop(), source='kyc.get_kyc_status')
        return BaseAPIResponse[Optional[KYCDocumentResponse]](
            data=doc_response,
            detail="KYC details retrieved successfully." if latest_kyc else "No KYC document submission found.",
            status_code=status.HTTP_200_OK,
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
