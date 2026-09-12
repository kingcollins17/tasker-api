from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.database import get_session
from app.core.deps import GetCurrentAdmin
from app.core.error_handler import AppErrorHandler
from app.core.models.admins import AdminUser
from app.core.models.support import CasePriority, CaseStatus, CaseType, MessageSenderType, MessageVisibility
from app.features.support.schemas import (
    CaseAssignmentCreate,
    CaseMessageCreate,
    CaseMessageResponse,
    CaseResolutionCreate,
    InternalNoteCreate,
    SupportCaseResponse,
    SupportCaseUpdate,
)
from app.features.support.services.assignment_service import CaseAssignmentService
from app.features.support.services.case_service import SupportCaseService
from app.features.support.services.dispute_service import DisputeService
from app.features.support.services.message_service import CaseMessageService
from app.features.support.services.resolution_service import CaseResolutionService
from app.features.support.celery import send_support_email_task

router = APIRouter()


@router.get("", response_model=BaseAPIResponse[PaginatedData[SupportCaseResponse]])
async def admin_list_cases(
    status_filter: Optional[CaseStatus] = Query(default=None, alias="status"),
    priority: Optional[CasePriority] = Query(default=None),
    type: Optional[CaseType] = Query(default=None),
    assigned_agent_id: Optional[str] = Query(default=None),
    customer_id: Optional[str] = Query(default=None),
    provider_id: Optional[str] = Query(default=None),
    task_id: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    try:
        service = SupportCaseService(session)
        cases, total = await service.list_cases(
            customer_id=customer_id,
            provider_id=provider_id,
            assigned_agent_id=assigned_agent_id,
            task_id=task_id,
            status=status_filter,
            priority=priority,
            type=type,
            search=search,
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
            detail="Failed to retrieve admin support cases",
        )


@router.get("/{case_id}", response_model=BaseAPIResponse[SupportCaseResponse])
async def admin_get_case(
    case_id: str,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    try:
        service = SupportCaseService(session)
        case = await service.get_case(case_id)
        if not case:
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


@router.post("/{case_id}/assign", response_model=BaseAPIResponse[SupportCaseResponse])
async def admin_assign_case(
    case_id: str,
    schema: CaseAssignmentCreate,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    try:
        service = CaseAssignmentService(session)
        case, _ = await service.assign_case(
            case_id=case_id,
            agent_id=schema.agent_id,
            assigned_by=admin.id,
            reason=schema.reason,
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
    except HTTPException as e:
        raise e
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to assign support case",
        )


@router.post("/{case_id}/messages", response_model=BaseAPIResponse[CaseMessageResponse])
async def admin_send_message(
    case_id: str,
    schema: CaseMessageCreate,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    try:
        msg_service = CaseMessageService(session)
        msg, case = await msg_service.send_message(
            case_id=case_id,
            sender_id=admin.id,
            sender_type=MessageSenderType.AGENT,
            body=schema.body,
            channel=schema.channel,
            visibility=MessageVisibility.PUBLIC,
        )
        if not msg or not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )

        # Queue outbound email if customer/provider email is available
        # pyrefly: ignore [not-callable]
        send_support_email_task.delay(
            to_email="customer@example.com",  # Email recipient placeholder or look up from user profile
            subject=f"Update on Case #{case.case_number}",
            body=schema.body,
            case_number=case.case_number,
            reply_token=case.reply_token,
        )

        return BaseAPIResponse.success_response(
            data=CaseMessageResponse.model_validate(msg),
            message="Agent message sent successfully",
        )
    except HTTPException as e:
        raise e
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send agent message",
        )


@router.post("/{case_id}/notes", response_model=BaseAPIResponse[CaseMessageResponse])
async def admin_add_internal_note(
    case_id: str,
    schema: InternalNoteCreate,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    try:
        msg_service = CaseMessageService(session)
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
    except HTTPException as e:
        raise e
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
    session: AsyncSession = Depends(get_session),
):
    try:
        service = SupportCaseService(session)
        case = await service.update_case(
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
    except HTTPException as e:
        raise e
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
    session: AsyncSession = Depends(get_session),
):
    try:
        service = CaseResolutionService(session)
        case, _ = await service.resolve_case(
            case_id=case_id,
            agent_id=admin.id,
            schema=schema,
        )
        if not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )
        return BaseAPIResponse.success_response(
            data=SupportCaseResponse.model_validate(case),
            message="Case resolved successfully",
        )
    except HTTPException as e:
        raise e
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
    session: AsyncSession = Depends(get_session),
):
    try:
        service = DisputeService(session)
        case = await service.escalate_dispute(case_id=case_id, agent_id=admin.id)
        if not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Support case not found",
            )
        return BaseAPIResponse.success_response(
            data=SupportCaseResponse.model_validate(case),
            message="Case escalated successfully",
        )
    except HTTPException as e:
        raise e
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to escalate support case",
        )
