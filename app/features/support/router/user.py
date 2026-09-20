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
from sqlalchemy.orm import aliased
from sqlmodel import and_, col, func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.database import get_session
from app.core.deps import GetCurrentUser
from app.core.error_handler import AppErrorHandler
from app.core.repository import GetRepository, QueryOptions, Repository
from app.core.models.support import (
    CaseAttachment,
    CaseEvent,
    CaseMessage,
    CaseStatus,
    MessageSenderType,
    MessageVisibility,
    SupportCase,
)
from app.core.models.users import CustomerProfile, ProviderProfile, User, UserType
from app.core.services.storage import StorageService, get_storage_service
from app.features.support.schemas import (
    CaseAttachmentResponse,
    CaseMessageCreate,
    CaseMessageResponse,
    InitiatorResponse,
    SupportCaseCreate,
    SupportCaseResponse,
    TimelineItemResponse,
)
from app.features.support.services.case_service import (
    SupportCaseService,
    get_support_case_service,
)
from app.features.support.services.message_service import (
    CaseMessageService,
    get_case_message_service,
)
from app.features.users.schemas import UserResponse

router = APIRouter()

InitiatorUser = aliased(User, name="initiator_user")
InitiatorCustomerProfile = aliased(CustomerProfile, name="initiator_customer_profile")
InitiatorProviderProfile = aliased(ProviderProfile, name="initiator_provider_profile")


def _map_case_with_initiator(
    case_obj: SupportCase,
    init_user_obj: Optional[User],
    init_cust_prof: Optional[CustomerProfile],
    init_prov_prof: Optional[ProviderProfile],
) -> SupportCaseResponse:
    resp = SupportCaseResponse.model_validate(case_obj)
    if init_user_obj:
        first_name = (init_cust_prof.first_name if init_cust_prof else None) or (init_prov_prof.first_name if init_prov_prof else None)
        last_name = (init_cust_prof.last_name if init_cust_prof else None) or (init_prov_prof.last_name if init_prov_prof else None)
        resp.initiator = InitiatorResponse(
            id=init_user_obj.id,
            first_name=first_name,
            last_name=last_name,
            email=init_user_obj.email,
            phone_number=init_user_obj.phone_number,
        )
    return resp


@router.post("/cases", response_model=BaseAPIResponse[SupportCaseResponse], status_code=status.HTTP_201_CREATED)
async def create_support_case(
    schema: SupportCaseCreate,
    current_user: UserResponse = Depends(GetCurrentUser()),
    service: SupportCaseService = Depends(get_support_case_service),
):
    try:
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
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create support case",
        )


@router.get("/cases", response_model=BaseAPIResponse[PaginatedData[SupportCaseResponse]])
async def list_user_cases(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    status_filter: Optional[List[str]] = Query(default=None, alias="status", description="Filter cases by status category ('open', 'closed') or list of CaseStatus values"),
    task_id: Optional[str] = Query(default=None),
    current_user: UserResponse = Depends(GetCurrentUser()),
    session: AsyncSession = Depends(get_session),
):
    try:
        is_customer = current_user.type == UserType.CUSTOMER
        customer_id = current_user.id if is_customer else None
        provider_id = current_user.id if not is_customer else None

        offset = (page - 1) * per_page
        init_user_id = func.coalesce(col(SupportCase.initiated_by), col(SupportCase.customer_id), col(SupportCase.provider_id))

        stmt = (
            select(SupportCase, InitiatorUser, InitiatorCustomerProfile, InitiatorProviderProfile)
            .outerjoin(InitiatorUser, init_user_id == col(InitiatorUser.id))
            .outerjoin(InitiatorCustomerProfile, col(InitiatorUser.id) == col(InitiatorCustomerProfile.user_id))
            .outerjoin(InitiatorProviderProfile, col(InitiatorUser.id) == col(InitiatorProviderProfile.user_id))
        )
        count_stmt = select(func.count()).select_from(SupportCase)

        filters = []
        if customer_id:
            filters.append(col(SupportCase.customer_id) == customer_id)
        if provider_id:
            filters.append(col(SupportCase.provider_id) == provider_id)
        if task_id:
            filters.append(col(SupportCase.task_id) == task_id)
        if status_filter:
            expanded_items = []
            for item in status_filter:
                if "," in item:
                    expanded_items.extend([s.strip() for s in item.split(",") if s.strip()])
                elif item.strip():
                    expanded_items.append(item.strip())

            target_statuses = set()
            open_statuses = {
                CaseStatus.OPEN,
                CaseStatus.IN_PROGRESS,
                CaseStatus.WAITING_FOR_CUSTOMER,
                CaseStatus.WAITING_FOR_PROVIDER,
                CaseStatus.WAITING_FOR_INTERNAL,
            }
            closed_statuses = {
                CaseStatus.RESOLVED,
                CaseStatus.CLOSED,
                CaseStatus.AUTO_CLOSED,
            }

            for item in expanded_items:
                item_lower = item.lower()
                if item_lower == "open":
                    target_statuses.update(open_statuses)
                elif item_lower == "closed":
                    target_statuses.update(closed_statuses)
                else:
                    try:
                        target_statuses.add(CaseStatus(item.upper()))
                    except ValueError:
                        try:
                            target_statuses.add(CaseStatus(item))
                        except ValueError:
                            pass

            if target_statuses:
                filters.append(col(SupportCase.status).in_(list(target_statuses)))

        if filters:
            stmt = stmt.where(and_(*filters))
            count_stmt = count_stmt.where(and_(*filters))

        total_res = await session.exec(count_stmt)
        total = total_res.one() or 0

        stmt = stmt.order_by(col(SupportCase.updated_at).desc()).limit(per_page).offset(offset)
        res = await session.exec(stmt)
        rows = res.all()

        items = [_map_case_with_initiator(c, u, cp, pp) for c, u, cp, pp in rows]
        return BaseAPIResponse.success_response(
            data=PaginatedData(items=items, total=total, page=page, per_page=per_page),
            message="Support cases retrieved successfully",
        )
    except HTTPException:
        raise
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
        init_user_id = func.coalesce(col(SupportCase.initiated_by), col(SupportCase.customer_id), col(SupportCase.provider_id))

        stmt = (
            select(SupportCase, InitiatorUser, InitiatorCustomerProfile, InitiatorProviderProfile)
            .outerjoin(InitiatorUser, init_user_id == col(InitiatorUser.id))
            .outerjoin(InitiatorCustomerProfile, col(InitiatorUser.id) == col(InitiatorCustomerProfile.user_id))
            .outerjoin(InitiatorProviderProfile, col(InitiatorUser.id) == col(InitiatorProviderProfile.user_id))
            .where(
                or_(
                    col(SupportCase.id) == case_id,
                    col(SupportCase.case_number) == case_id,
                )
            )
        )
        res = await session.exec(stmt)
        row = res.first()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )
        case_obj, init_user_obj, init_cust_prof, init_prov_prof = row
        if case_obj.customer_id != current_user.id and case_obj.provider_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )
        return BaseAPIResponse.success_response(
            data=_map_case_with_initiator(case_obj, init_user_obj, init_cust_prof, init_prov_prof),
            message="Support case retrieved successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve support case",
        )


@router.get("/cases/{case_id}/messages", response_model=BaseAPIResponse[PaginatedData[CaseMessageResponse]])
async def get_case_messages(
    case_id: str,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    current_user: UserResponse = Depends(GetCurrentUser()),
    session: AsyncSession = Depends(get_session),
):
    try:
        stmt_case = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        res_case = await session.exec(stmt_case)
        case = res_case.first()
        if not case or (case.customer_id != current_user.id and case.provider_id != current_user.id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )

        is_customer = current_user.type == UserType.CUSTOMER
        allowed_visibilities = [
            MessageVisibility.PUBLIC,
            MessageVisibility.CUSTOMER_ONLY if is_customer else MessageVisibility.PROVIDER_ONLY,
        ]

        offset = (page - 1) * per_page
        count_stmt = select(func.count()).select_from(CaseMessage).where(
            col(CaseMessage.case_id) == case.id,
            col(CaseMessage.visibility).in_(allowed_visibilities),
        )
        total = (await session.exec(count_stmt)).one() or 0

        stmt_msg = (
            select(CaseMessage)
            .where(
                col(CaseMessage.case_id) == case.id,
                col(CaseMessage.visibility).in_(allowed_visibilities),
            )
            .order_by(col(CaseMessage.created_at).asc())
            .limit(per_page)
            .offset(offset)
        )
        res_msg = await session.exec(stmt_msg)
        messages = res_msg.all()

        items = [CaseMessageResponse.model_validate(m) for m in messages]
        return BaseAPIResponse.success_response(
            data=PaginatedData(items=items, total=total, page=page, per_page=per_page),
            message="Case messages retrieved successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve case messages",
        )


@router.get("/cases/{case_id}/attachments", response_model=BaseAPIResponse[PaginatedData[CaseAttachmentResponse]])
async def get_user_case_attachments(
    case_id: str,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    current_user: UserResponse = Depends(GetCurrentUser()),
    case_repo: Repository[SupportCase] = Depends(GetRepository(SupportCase)),
    attachment_repo: Repository[CaseAttachment] = Depends(GetRepository(CaseAttachment)),
):
    """Retrieves all file attachments for a user's support case."""
    try:
        case = await case_repo.get(case_id)
        if not case or (case.customer_id != current_user.id and case.provider_id != current_user.id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )

        offset = (page - 1) * per_page
        all_attachments = await attachment_repo.get_all(
            QueryOptions(
                filters={"case_id": case.id},
                order_by="created_at",
                descending=True,
            )
        )
        total = len(all_attachments)
        paginated_attachments = all_attachments[offset : offset + per_page]

        items = [CaseAttachmentResponse.model_validate(a) for a in paginated_attachments]
        return BaseAPIResponse.success_response(
            data=PaginatedData(items=items, total=total, page=page, per_page=per_page),
            message="Case attachments retrieved successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve case attachments",
        )


@router.get("/cases/{case_id}/timeline", response_model=BaseAPIResponse[PaginatedData[TimelineItemResponse]])
async def get_case_timeline(
    case_id: str,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    current_user: UserResponse = Depends(GetCurrentUser()),
    session: AsyncSession = Depends(get_session),
):
    try:
        stmt_case = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        res_case = await session.exec(stmt_case)
        case = res_case.first()
        if not case or (case.customer_id != current_user.id and case.provider_id != current_user.id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )

        is_customer = current_user.type == UserType.CUSTOMER
        allowed_visibilities = [
            MessageVisibility.PUBLIC,
            MessageVisibility.CUSTOMER_ONLY if is_customer else MessageVisibility.PROVIDER_ONLY,
        ]

        stmt_events = select(CaseEvent).where(col(CaseEvent.case_id) == case.id)
        events = (await session.exec(stmt_events)).all()

        stmt_msgs = select(CaseMessage).where(
            col(CaseMessage.case_id) == case.id,
            col(CaseMessage.visibility).in_(allowed_visibilities),
        )
        messages = (await session.exec(stmt_msgs)).all()

        items: List[TimelineItemResponse] = []
        for e in events:
            items.append(
                TimelineItemResponse(
                    id=e.id,
                    item_type="EVENT",
                    timestamp=e.created_at,
                    title=f"Event: {e.event_type.value}",
                    description=None,
                    actor_type=e.actor_type,
                    actor_id=e.actor_id,
                    metadata=e.event_metadata,
                )
            )
        for m in messages:
            items.append(
                TimelineItemResponse(
                    id=m.id,
                    item_type="MESSAGE",
                    timestamp=m.created_at,
                    title=f"Message ({m.sender_type.value})",
                    description=m.body,
                    actor_type=m.sender_type.value,
                    actor_id=m.sender_id,
                    metadata={"channel": m.channel.value, "visibility": m.visibility.value},
                )
            )

        items.sort(key=lambda x: x.timestamp)
        total = len(items)
        offset = (page - 1) * per_page
        paginated_items = items[offset : offset + per_page]

        return BaseAPIResponse.success_response(
            data=PaginatedData(items=paginated_items, total=total, page=page, per_page=per_page),
            message="Case timeline retrieved successfully",
        )
    except HTTPException:
        raise
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
    msg_service: CaseMessageService = Depends(get_case_message_service),
):
    try:
        stmt = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        case = (await session.exec(stmt)).first()
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
    except HTTPException:
        raise
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
        stmt = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        case = (await session.exec(stmt)).first()
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
    except HTTPException:
        raise
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
    service: SupportCaseService = Depends(get_support_case_service),
):
    try:
        stmt = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        case = (await session.exec(stmt)).first()
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
    except HTTPException:
        raise
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
    service: SupportCaseService = Depends(get_support_case_service),
):
    try:
        stmt = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        case = (await session.exec(stmt)).first()
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
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reopen support case",
        )
