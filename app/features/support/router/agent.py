from typing import List, Optional
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import aliased
from sqlmodel import and_, col, func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.database import get_session
from app.core.deps import GetCurrentAdmin
from app.core.error_handler import AppErrorHandler
from app.core.repository import GetRepository, QueryOptions, Repository
from app.core.models.admins import AdminRole, AdminUser
from app.core.models.payments import PayoutQueue
from app.core.models.support import (
    CaseAssignment,
    CaseAttachment,
    CaseEvent,
    CaseMessage,
    CasePriority,
    CaseStatus,
    CaseType,
    Dispute,
    MessageSenderType,
    MessageVisibility,
    SupportCase,
)
from app.core.models.tasks import Task
from app.core.models.users import CustomerProfile, ProviderProfile, User
from app.core.services.storage import StorageService, get_storage_service
from app.features.support.celery import send_support_email_task
from app.features.support.schemas import (
    AdminCaseMessageCreate,
    CaseAssignmentCreate,
    CaseAttachmentResponse,
    CaseMessageCreate,
    CaseMessageResponse,
    CaseResolutionCreate,
    InitiatorResponse,
    InternalNoteCreate,
    SupportCaseDetailResponse,
    SupportCaseResponse,
    SupportCaseUpdate,
    TimelineItemResponse,
)
from app.features.support.services.assignment_service import (
    CaseAssignmentService,
    get_case_assignment_service,
)
from app.features.support.services.case_service import (
    SupportCaseService,
    get_support_case_service,
)
from app.features.support.services.dispute_service import (
    DisputeService,
    get_dispute_service,
)
from app.features.support.services.message_service import (
    CaseMessageService,
    get_case_message_service,
)
from app.features.support.services.resolution_service import (
    CaseResolutionService,
    get_case_resolution_service,
)

router = APIRouter()

CustomerUser = aliased(User, name="customer_user")
ProviderUser = aliased(User, name="provider_user")
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


@router.get("", response_model=BaseAPIResponse[PaginatedData[SupportCaseResponse]])
@router.get("/cases", response_model=BaseAPIResponse[PaginatedData[SupportCaseResponse]], include_in_schema=False)
async def admin_list_cases(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    status_filter: Optional[List[CaseStatus]] = Query(default=None, alias="status"),
    priority: Optional[CasePriority] = Query(default=None),
    type: Optional[CaseType] = Query(default=None),
    assigned_agent_id: Optional[str] = Query(default=None),
    customer_id: Optional[str] = Query(default=None),
    provider_id: Optional[str] = Query(default=None),
    task_id: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """Lists all support cases with extensive filtering and pagination."""
    try:
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
        if assigned_agent_id:
            filters.append(col(SupportCase.assigned_agent_id) == assigned_agent_id)
        if task_id:
            filters.append(col(SupportCase.task_id) == task_id)
        if status_filter:
            filters.append(col(SupportCase.status).in_(status_filter))
        if priority:
            filters.append(col(SupportCase.priority) == priority)
        if type:
            filters.append(col(SupportCase.type) == type)
        if search:
            filters.append(
                or_(
                    col(SupportCase.subject).ilike(f"%{search}%"),
                    col(SupportCase.case_number).ilike(f"%{search}%"),
                )
            )

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
            detail="Failed to retrieve admin support cases",
        )


@router.get("/my-cases", response_model=BaseAPIResponse[PaginatedData[SupportCaseResponse]])
@router.get("/assigned", response_model=BaseAPIResponse[PaginatedData[SupportCaseResponse]], include_in_schema=False)
async def admin_list_assigned_cases(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    status_filter: Optional[List[CaseStatus]] = Query(default=None, alias="status"),
    priority: Optional[CasePriority] = Query(default=None),
    type: Optional[CaseType] = Query(default=None),
    search: Optional[str] = Query(default=None),
    admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """Lists support tickets assigned to the requesting admin."""
    try:
        offset = (page - 1) * per_page
        init_user_id = func.coalesce(col(SupportCase.initiated_by), col(SupportCase.customer_id), col(SupportCase.provider_id))

        stmt = (
            select(SupportCase, InitiatorUser, InitiatorCustomerProfile, InitiatorProviderProfile)
            .outerjoin(InitiatorUser, init_user_id == col(InitiatorUser.id))
            .outerjoin(InitiatorCustomerProfile, col(InitiatorUser.id) == col(InitiatorCustomerProfile.user_id))
            .outerjoin(InitiatorProviderProfile, col(InitiatorUser.id) == col(InitiatorProviderProfile.user_id))
            .where(col(SupportCase.assigned_agent_id) == admin.id)
        )
        count_stmt = select(func.count()).select_from(SupportCase).where(col(SupportCase.assigned_agent_id) == admin.id)

        filters = []
        if status_filter:
            filters.append(col(SupportCase.status).in_(status_filter))
        if priority:
            filters.append(col(SupportCase.priority) == priority)
        if type:
            filters.append(col(SupportCase.type) == type)
        if search:
            filters.append(
                or_(
                    col(SupportCase.subject).ilike(f"%{search}%"),
                    col(SupportCase.case_number).ilike(f"%{search}%"),
                )
            )

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
            message="Assigned support cases retrieved successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve assigned support cases",
        )


@router.get("/{case_id}", response_model=BaseAPIResponse[SupportCaseDetailResponse])
async def admin_get_case(
    case_id: str,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """Retrieves a single support case with stitched related entities (task, customer, provider, initiator, assignment, payout) via LEFT JOIN."""
    try:
        init_user_id = func.coalesce(col(SupportCase.initiated_by), col(SupportCase.customer_id), col(SupportCase.provider_id))

        stmt = (
            # pyrefly: ignore [no-matching-overload]
            select(
                SupportCase,
                Task,
                CustomerUser,
                ProviderUser,
                InitiatorUser,
                InitiatorCustomerProfile,
                InitiatorProviderProfile,
                CaseAssignment,
                PayoutQueue,
            )
            .outerjoin(Task, col(SupportCase.task_id) == col(Task.id))
            .outerjoin(CustomerUser, col(SupportCase.customer_id) == col(CustomerUser.id))
            .outerjoin(ProviderUser, col(SupportCase.provider_id) == col(ProviderUser.id))
            .outerjoin(InitiatorUser, init_user_id == col(InitiatorUser.id))
            .outerjoin(InitiatorCustomerProfile, col(InitiatorUser.id) == col(InitiatorCustomerProfile.user_id))
            .outerjoin(InitiatorProviderProfile, col(InitiatorUser.id) == col(InitiatorProviderProfile.user_id))
            .outerjoin(CaseAssignment, or_(col(SupportCase.assignment_id) == col(CaseAssignment.id), col(SupportCase.id) == col(CaseAssignment.case_id)))
            .outerjoin(PayoutQueue, col(SupportCase.payout_id) == col(PayoutQueue.id))
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

        case_obj, task_obj, customer_obj, provider_obj, init_user_obj, init_cust_prof, init_prov_prof, assignment_obj, payout_obj = row

        initiator_data = None
        if init_user_obj:
            first_name = (init_cust_prof.first_name if init_cust_prof else None) or (init_prov_prof.first_name if init_prov_prof else None)
            last_name = (init_cust_prof.last_name if init_cust_prof else None) or (init_prov_prof.last_name if init_prov_prof else None)
            initiator_data = InitiatorResponse(
                id=init_user_obj.id,
                first_name=first_name,
                last_name=last_name,
                email=init_user_obj.email,
                phone_number=init_user_obj.phone_number,
            )

        detail = SupportCaseDetailResponse(
            **case_obj.model_dump(),
            task=task_obj.model_dump() if task_obj else None,
            customer=customer_obj.model_dump() if customer_obj else None,
            provider=provider_obj.model_dump() if provider_obj else None,
            initiator=initiator_data,
            assignment=assignment_obj.model_dump() if assignment_obj else None,
            payout=payout_obj.model_dump() if payout_obj else None,
        )

        return BaseAPIResponse.success_response(
            data=detail,
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


@router.get("/{case_id}/messages", response_model=BaseAPIResponse[PaginatedData[CaseMessageResponse]])
async def admin_get_messages(
    case_id: str,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """Retrieves messages for a support case."""
    try:
        stmt_case = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        res_case = await session.exec(stmt_case)
        case = res_case.first()
        if not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )

        offset = (page - 1) * per_page
        count_stmt = select(func.count()).select_from(CaseMessage).where(col(CaseMessage.case_id) == case.id)
        total_res = await session.exec(count_stmt)
        total = total_res.one() or 0

        stmt = (
            select(CaseMessage)
            .where(col(CaseMessage.case_id) == case.id)
            .order_by(col(CaseMessage.created_at).asc())
            .limit(per_page)
            .offset(offset)
        )
        res = await session.exec(stmt)
        messages = res.all()

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


@router.get("/{case_id}/timeline", response_model=BaseAPIResponse[PaginatedData[TimelineItemResponse]])
async def admin_get_timeline(
    case_id: str,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """Retrieves full audit timeline items (events & messages) for a case."""
    try:
        stmt_case = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        res_case = await session.exec(stmt_case)
        case = res_case.first()
        if not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )

        stmt_events = select(CaseEvent).where(col(CaseEvent.case_id) == case.id)
        events = (await session.exec(stmt_events)).all()

        stmt_msgs = select(CaseMessage).where(col(CaseMessage.case_id) == case.id)
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


@router.get("/{case_id}/attachments", response_model=BaseAPIResponse[PaginatedData[CaseAttachmentResponse]])
async def admin_get_case_attachments(
    case_id: str,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=100, ge=1, le=100),
    admin: AdminUser = Depends(GetCurrentAdmin()),
    case_repo: Repository[SupportCase] = Depends(GetRepository(SupportCase)),
    attachment_repo: Repository[CaseAttachment] = Depends(GetRepository(CaseAttachment)),
):
    """Retrieves all file attachments for a specific support case."""
    try:
        case = await case_repo.get(case_id)
        if not case:
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


@router.post("/{case_id}/claim", response_model=BaseAPIResponse[SupportCaseResponse])
async def admin_claim_case(
    case_id: str,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    assignment_service: CaseAssignmentService = Depends(get_case_assignment_service),
):
    """Claims an open support case, assigning it to the requesting admin and setting status to IN_PROGRESS."""
    try:
        case, _ = await assignment_service.assign_case(
            case_id=case_id,
            agent_id=admin.id,
            assigned_by=admin.id,
            reason="Claimed by admin",
            overwrite_existing_assignment=False,
        )
        if not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )
        return BaseAPIResponse.success_response(
            data=SupportCaseResponse.model_validate(case),
            message="Support case claimed successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to claim support case",
        )


@router.post("/{case_id}/assign", response_model=BaseAPIResponse[SupportCaseResponse])
async def admin_assign_case(
    case_id: str,
    schema: CaseAssignmentCreate,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    assignment_service: CaseAssignmentService = Depends(get_case_assignment_service),
):
    """Assigns a support case to a specific agent."""
    try:
        is_super_admin = admin.role in [AdminRole.SUPER_ADMIN, AdminRole.ROOT_ADMIN]
        case, _ = await assignment_service.assign_case(
            case_id=case_id,
            agent_id=schema.agent_id,
            assigned_by=admin.id,
            reason=schema.reason,
            overwrite_existing_assignment=is_super_admin,
        )
        if not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )
        return BaseAPIResponse.success_response(
            data=SupportCaseResponse.model_validate(case),
            message="Case assigned successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to assign support case",
        )


@router.post("/{case_id}/messages", response_model=BaseAPIResponse[CaseMessageResponse])
async def admin_send_message(
    case_id: str,
    schema: AdminCaseMessageCreate,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    msg_service: CaseMessageService = Depends(get_case_message_service),
):
    """Sends a message from an admin, enabling custom visibility, optional attachments, and status updates (e.g. WAITING_FOR_USER, WAITING_FOR_PROVIDER)."""
    try:
        msg, case = await msg_service.send_message(
            case_id=case_id,
            sender_id=admin.id,
            sender_type=MessageSenderType.AGENT,
            body=schema.body,
            channel=schema.channel,
            visibility=schema.visibility,
            status_update=schema.status_update,
            attachment_ids=schema.attachment_ids,
        )
        if not msg or not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )

        # Queue outbound email task if applicable
        # pyrefly: ignore [not-callable]
        send_support_email_task.delay(
            to_email="customer@example.com",
            subject=f"Update on Case #{case.case_number}",
            body=schema.body,
            case_number=case.case_number,
            reply_token=case.reply_token,
        )

        return BaseAPIResponse.success_response(
            data=CaseMessageResponse.model_validate(msg),
            message="Agent message sent successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send agent message",
        )


@router.post("/{case_id}/attachments", response_model=BaseAPIResponse[CaseAttachmentResponse])
async def admin_upload_attachment(
    case_id: str,
    file: UploadFile = File(...),
    admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
    storage_service: StorageService = Depends(get_storage_service),
):
    """Uploads an attachment file for a support case."""
    try:
        stmt = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        case = (await session.exec(stmt)).first()
        if not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )

        file_bytes = await file.read()
        filename = file.filename or "attachment"
        storage_key = await storage_service.upload_file(file=file_bytes, filename=filename)

        attachment = CaseAttachment(
            case_id=case.id,
            uploaded_by=admin.id,
            storage_key=storage_key,
            filename=file.filename or "attachment",
            mime_type=file.content_type or "application/octet-stream",
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
            detail="Failed to upload case attachment",
        )


@router.post("/{case_id}/notes", response_model=BaseAPIResponse[CaseMessageResponse])
async def admin_add_internal_note(
    case_id: str,
    schema: InternalNoteCreate,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    msg_service: CaseMessageService = Depends(get_case_message_service),
):
    """Adds an internal note visible only to support agents."""
    try:
        msg, _ = await msg_service.add_internal_note(
            case_id=case_id,
            agent_id=admin.id,
            body=schema.body,
        )
        if not msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )
        return BaseAPIResponse.success_response(
            data=CaseMessageResponse.model_validate(msg),
            message="Internal note added successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add internal note",
        )


@router.patch("/{case_id}", response_model=BaseAPIResponse[SupportCaseResponse])
async def admin_update_case(
    case_id: str,
    schema: SupportCaseUpdate,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    case_service: SupportCaseService = Depends(get_support_case_service),
):
    """Updates case properties (status, priority, subject, description)."""
    try:
        case = await case_service.update_case(
            case_id=case_id,
            schema=schema,
            actor_id=admin.id,
            actor_type="AGENT",
        )
        if not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )
        return BaseAPIResponse.success_response(
            data=SupportCaseResponse.model_validate(case),
            message="Support case updated successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update support case",
        )


@router.post("/{case_id}/resolve", response_model=BaseAPIResponse[SupportCaseResponse])
async def admin_resolve_case(
    case_id: str,
    schema: CaseResolutionCreate,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    resolution_service: CaseResolutionService = Depends(get_case_resolution_service),
    session: AsyncSession = Depends(get_session),
):
    """Resolves a support ticket and any linked dispute."""
    try:
        case, _ = await resolution_service.resolve_case(
            case_id=case_id,
            agent_id=admin.id,
            schema=schema,
        )
        if not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )

        # Check if case is linked to a dispute and update dispute status if needed
        stmt_disp = select(Dispute).where(col(Dispute.case_id) == case.id)
        dispute = (await session.exec(stmt_disp)).first()
        if dispute:
            dispute.requested_resolution = schema.decision
            session.add(dispute)
            await session.commit()

        return BaseAPIResponse.success_response(
            data=SupportCaseResponse.model_validate(case),
            message="Case and dispute resolved successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resolve support case",
        )


@router.post("/{case_id}/escalate", response_model=BaseAPIResponse[SupportCaseResponse])
async def admin_escalate_case(
    case_id: str,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    dispute_service: DisputeService = Depends(get_dispute_service),
):
    """Escalates a support case dispute priority to URGENT."""
    try:
        case = await dispute_service.escalate_dispute(case_id=case_id, agent_id=admin.id)
        if not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )
        return BaseAPIResponse.success_response(
            data=SupportCaseResponse.model_validate(case),
            message="Case escalated successfully",
        )
    except HTTPException:
        raise
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to escalate support case",
        )
