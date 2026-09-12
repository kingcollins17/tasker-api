from typing import List, Optional
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.database import get_session
from app.core.deps import GetCurrentUser
from app.core.error_handler import AppErrorHandler
from app.core.models.support import CaseAttachment, MessageSenderType
from app.core.models.users import UserType
from app.core.services.storage import StorageService, get_storage_service
from app.features.support.schemas import (
    CaseAttachmentResponse,
    CaseMessageCreate,
    CaseMessageResponse,
    SupportCaseCreate,
    SupportCaseResponse,
    TimelineItemResponse,
)
from app.features.support.services.case_service import SupportCaseService
from app.features.support.services.message_service import CaseMessageService
from app.features.users.schemas import UserResponse

router = APIRouter()


@router.post("/cases", response_model=BaseAPIResponse[SupportCaseResponse], status_code=status.HTTP_201_CREATED)
async def create_support_case(
    schema: SupportCaseCreate,
    current_user: UserResponse = Depends(GetCurrentUser()),
    session: AsyncSession = Depends(get_session),
):
    try:
        service = SupportCaseService(session)
        is_customer = current_user.type == UserType.CUSTOMER
        case = await service.create_case(
            user_id=current_user.id,
            is_customer=is_customer,
            schema=schema,
        )
        return BaseAPIResponse.success_response(
            data=SupportCaseResponse.model_validate(case),
            message="Support case created successfully",
        )
    except HTTPException as e:
        raise e
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create support case",
        )


@router.get("/cases", response_model=BaseAPIResponse[PaginatedData[SupportCaseResponse]])
async def list_user_cases(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: UserResponse = Depends(GetCurrentUser()),
    session: AsyncSession = Depends(get_session),
):
    try:
        service = SupportCaseService(session)
        is_customer = current_user.type == UserType.CUSTOMER
        customer_id = current_user.id if is_customer else None
        provider_id = current_user.id if not is_customer else None

        cases, total = await service.list_cases(
            customer_id=customer_id,
            provider_id=provider_id,
            limit=limit,
            offset=offset,
        )
        items = [SupportCaseResponse.model_validate(c) for c in cases]
        return BaseAPIResponse.success_response(
            data=PaginatedData(items=items, total=total, limit=limit, offset=offset),
            message="Support cases retrieved successfully",
        )
    except HTTPException as e:
        raise e
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve support cases",
        )


@router.get("/cases/{case_id}", response_model=BaseAPIResponse[SupportCaseResponse])
async def get_user_case(
    case_id: str,
    current_user: UserResponse = Depends(GetCurrentUser()),
    session: AsyncSession = Depends(get_session),
):
    try:
        service = SupportCaseService(session)
        case = await service.get_case(case_id)
        if not case or (case.customer_id != current_user.id and case.provider_id != current_user.id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )
        return BaseAPIResponse.success_response(
            data=SupportCaseResponse.model_validate(case),
            message="Support case retrieved successfully",
        )
    except HTTPException as e:
        raise e
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve support case",
        )


@router.get("/cases/{case_id}/messages", response_model=BaseAPIResponse[List[CaseMessageResponse]])
async def get_case_messages(
    case_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: UserResponse = Depends(GetCurrentUser()),
    session: AsyncSession = Depends(get_session),
):
    try:
        case_service = SupportCaseService(session)
        case = await case_service.get_case(case_id)
        if not case or (case.customer_id != current_user.id and case.provider_id != current_user.id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )

        msg_service = CaseMessageService(session)
        messages = await msg_service.get_messages(
            case_id=case.id,
            is_admin=False,
            limit=limit,
            offset=offset,
        )
        return BaseAPIResponse.success_response(
            data=[CaseMessageResponse.model_validate(m) for m in messages],
            message="Case messages retrieved successfully",
        )
    except HTTPException as e:
        raise e
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve case messages",
        )


@router.get("/cases/{case_id}/timeline", response_model=BaseAPIResponse[List[TimelineItemResponse]])
async def get_case_timeline(
    case_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: UserResponse = Depends(GetCurrentUser()),
    session: AsyncSession = Depends(get_session),
):
    try:
        case_service = SupportCaseService(session)
        case = await case_service.get_case(case_id)
        if not case or (case.customer_id != current_user.id and case.provider_id != current_user.id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )

        timeline = await case_service.get_timeline(
            case_id=case.id,
            is_admin=False,
            limit=limit,
            offset=offset,
        )
        return BaseAPIResponse.success_response(
            data=timeline,
            message="Case timeline retrieved successfully",
        )
    except HTTPException as e:
        raise e
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve case timeline",
        )


@router.post("/cases/{case_id}/messages", response_model=BaseAPIResponse[CaseMessageResponse])
async def send_user_message(
    case_id: str,
    schema: CaseMessageCreate,
    current_user: UserResponse = Depends(GetCurrentUser()),
    session: AsyncSession = Depends(get_session),
):
    try:
        case_service = SupportCaseService(session)
        case = await case_service.get_case(case_id)
        if not case or (case.customer_id != current_user.id and case.provider_id != current_user.id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )

        sender_type = (
            MessageSenderType.CUSTOMER
            if current_user.type == UserType.CUSTOMER
            else MessageSenderType.PROVIDER
        )
        msg_service = CaseMessageService(session)
        msg, _ = await msg_service.send_message(
            case_id=case.id,
            sender_id=current_user.id,
            sender_type=sender_type,
            body=schema.body,
            channel=schema.channel,
        )
        if not msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to send message",
            )
        return BaseAPIResponse.success_response(
            data=CaseMessageResponse.model_validate(msg),
            message="Message sent successfully",
        )
    except HTTPException as e:
        raise e
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send case message",
        )


@router.post("/cases/{case_id}/attachments", response_model=BaseAPIResponse[CaseAttachmentResponse])
async def upload_case_attachment(
    case_id: str,
    file: UploadFile = File(...),
    message_id: Optional[str] = Query(default=None),
    current_user: UserResponse = Depends(GetCurrentUser()),
    session: AsyncSession = Depends(get_session),
    storage_service: StorageService = Depends(get_storage_service),
):
    try:
        case_service = SupportCaseService(session)
        case = await case_service.get_case(case_id)
        if not case or (case.customer_id != current_user.id and case.provider_id != current_user.id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )

        file_bytes = await file.read()
        filename = file.filename or "attachment"
        content_type = file.content_type or "application/octet-stream"

        key = await storage_service.upload_file(
            file=file_bytes,
            filename=filename,
            content_type=content_type,
            folder=f"support_cases/{case.id}",
        )

        attachment = CaseAttachment(
            case_id=case.id,
            message_id=message_id,
            uploaded_by=current_user.id,
            storage_key=key,
            filename=filename,
            mime_type=content_type,
            size=len(file_bytes),
        )
        session.add(attachment)
        await session.commit()
        await session.refresh(attachment)

        return BaseAPIResponse.success_response(
            data=CaseAttachmentResponse.model_validate(attachment),
            message="Attachment uploaded successfully",
        )
    except HTTPException as e:
        raise e
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload attachment",
        )


@router.post("/cases/{case_id}/close", response_model=BaseAPIResponse[SupportCaseResponse])
async def close_user_case(
    case_id: str,
    current_user: UserResponse = Depends(GetCurrentUser()),
    session: AsyncSession = Depends(get_session),
):
    try:
        service = SupportCaseService(session)
        case = await service.get_case(case_id)
        if not case or (case.customer_id != current_user.id and case.provider_id != current_user.id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )

        actor_type = "CUSTOMER" if current_user.type == UserType.CUSTOMER else "PROVIDER"
        closed_case = await service.close_case(case.id, current_user.id, actor_type)
        return BaseAPIResponse.success_response(
            data=SupportCaseResponse.model_validate(closed_case),
            message="Support case closed successfully",
        )
    except HTTPException as e:
        raise e
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to close support case",
        )


@router.post("/cases/{case_id}/reopen", response_model=BaseAPIResponse[SupportCaseResponse])
async def reopen_user_case(
    case_id: str,
    current_user: UserResponse = Depends(GetCurrentUser()),
    session: AsyncSession = Depends(get_session),
):
    try:
        service = SupportCaseService(session)
        case = await service.get_case(case_id)
        if not case or (case.customer_id != current_user.id and case.provider_id != current_user.id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )

        actor_type = "CUSTOMER" if current_user.type == UserType.CUSTOMER else "PROVIDER"
        reopened_case = await service.reopen_case(case.id, current_user.id, actor_type)
        return BaseAPIResponse.success_response(
            data=SupportCaseResponse.model_validate(reopened_case),
            message="Support case reopened successfully",
        )
    except HTTPException as e:
        raise e
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reopen support case",
        )
