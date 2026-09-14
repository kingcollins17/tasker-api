from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlmodel import col, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.api_response import BaseAPIResponse, PaginatedData
from app.core.database import get_session
from app.core.deps import GetCurrentAdmin
from app.core.error_handler import AppErrorHandler
from app.core.models.admins import AdminUser
from app.core.models.notifications import NotificationType
from app.core.models.users import KYCDocument, KYCStatus, VerificationStatus
from app.core.models.vetting import InterviewStatus, ProviderGuarantor, ProviderInterview
from app.features.notifications.notification_service import (
    NotificationService,
    get_notification_service,
)
from app.features.users.schemas import KYCDocumentResponse
from app.features.users.services.kyc_service import KYCService, get_kyc_service
from app.features.vetting.interview_manager_service import (
    InterviewManagerService,
    get_interview_manager_service,
)
from app.features.vetting.schemas import (
    ApproveGuarantorRequest,
    ApproveKYCRequest,
    GuarantorResponse,
    InterviewResponse,
    RejectGuarantorRequest,
    RejectKYCRequest,
    ScheduleInterviewRequest,
    UpdateInterviewStatusRequest,
)
from app.features.vetting.vetting_service import VettingService, get_vetting_service

router = APIRouter(prefix="/admin", tags=["Vetting - Admin"])


@router.post(
    "/guarantors/{guarantor_id}/approve",
    response_model=BaseAPIResponse[GuarantorResponse],
    status_code=status.HTTP_200_OK,
)
async def approve_guarantor(
    guarantor_id: str,
    schema: ApproveGuarantorRequest,
    bg: BackgroundTasks,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    vetting_service: VettingService = Depends(get_vetting_service),
    ns: NotificationService = Depends(get_notification_service),
):
    """Approve a provider's guarantor submission and notify provider via background task."""
    try:
        guarantor = await vetting_service.approve_guarantor(
            guarantor_id=guarantor_id, notes=schema.notes
        )
        bg.add_task(
            ns.notify,
            recepients=[guarantor.provider_id],
            title="Guarantor Verification Approved",
            body="Your guarantor submission has been approved.",
            type=NotificationType.SYSTEM_ALERT,
            data={"guarantor_id": guarantor.id},
        )
        return BaseAPIResponse[GuarantorResponse](
            data=GuarantorResponse.model_validate(guarantor),
            detail="Guarantor verification approved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to approve guarantor verification.",
        )


@router.post(
    "/guarantors/{guarantor_id}/reject",
    response_model=BaseAPIResponse[GuarantorResponse],
    status_code=status.HTTP_200_OK,
)
async def reject_guarantor(
    guarantor_id: str,
    schema: RejectGuarantorRequest,
    bg: BackgroundTasks,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    vetting_service: VettingService = Depends(get_vetting_service),
    ns: NotificationService = Depends(get_notification_service),
):
    """Reject a provider's guarantor submission and notify provider via background task."""
    try:
        guarantor = await vetting_service.reject_guarantor(
            guarantor_id=guarantor_id, reason=schema.reason, notes=schema.notes
        )
        bg.add_task(
            ns.notify,
            recepients=[guarantor.provider_id],
            title="Guarantor Verification Rejected",
            body=f"Your guarantor submission was rejected: {schema.reason}",
            type=NotificationType.SYSTEM_ALERT,
            data={"guarantor_id": guarantor.id, "reason": schema.reason},
        )
        return BaseAPIResponse[GuarantorResponse](
            data=GuarantorResponse.model_validate(guarantor),
            detail="Guarantor verification rejected successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reject guarantor verification.",
        )


@router.post(
    "/kyc/{user_id}/approve",
    response_model=BaseAPIResponse[dict],
    status_code=status.HTTP_200_OK,
)
async def approve_kyc(
    user_id: str,
    bg: BackgroundTasks,
    schema: Optional[ApproveKYCRequest] = None,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    kyc_service: KYCService = Depends(get_kyc_service),
    ns: NotificationService = Depends(get_notification_service),
):
    """Approve provider KYC document verification and notify user via background task."""
    try:
        notes = schema.notes if schema else None
        doc = await kyc_service.approve_kyc(
            user_id=user_id, reviewer_id=admin.id, notes=notes
        )
        bg.add_task(
            ns.notify,
            recepients=[user_id],
            title="KYC Verification Approved",
            body="Your KYC document verification attempt has been approved.",
            type=NotificationType.SYSTEM_ALERT,
            data={"user_id": user_id, "document_id": doc.id},
        )
        return BaseAPIResponse[dict](
            data={"user_id": user_id, "document_id": doc.id, "status": doc.status.value},
            detail="KYC verification approved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to approve KYC verification.",
        )


@router.post(
    "/kyc/{user_id}/reject",
    response_model=BaseAPIResponse[dict],
    status_code=status.HTTP_200_OK,
)
async def reject_kyc(
    user_id: str,
    schema: RejectKYCRequest,
    bg: BackgroundTasks,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    kyc_service: KYCService = Depends(get_kyc_service),
    ns: NotificationService = Depends(get_notification_service),
):
    """Reject provider KYC document verification and notify user via background task."""
    try:
        doc = await kyc_service.reject_kyc(
            user_id=user_id,
            rejection_reason=schema.reason,
            reviewer_id=admin.id,
            metadata={"notes": schema.notes} if schema.notes else None,
        )
        bg.add_task(
            ns.notify,
            recepients=[user_id],
            title="KYC Verification Rejected",
            body=f"Your KYC document verification was rejected: {schema.reason}",
            type=NotificationType.SYSTEM_ALERT,
            data={"user_id": user_id, "reason": schema.reason, "document_id": doc.id},
        )
        return BaseAPIResponse[dict](
            data={"user_id": user_id, "document_id": doc.id, "status": doc.status.value, "rejection_reason": schema.reason},
            detail="KYC verification rejected successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reject KYC verification.",
        )


@router.post(
    "/interviews/schedule",
    response_model=BaseAPIResponse[InterviewResponse],
    status_code=status.HTTP_201_CREATED,
)
async def schedule_interview(
    schema: ScheduleInterviewRequest,
    bg: BackgroundTasks,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    interview_manager: InterviewManagerService = Depends(get_interview_manager_service),
    ns: NotificationService = Depends(get_notification_service),
):
    """Schedule an online interview for a KYC-verified user and notify user in background task."""
    try:
        interview = await interview_manager.schedule_interview(
            user_id=schema.user_id,
            scheduled_at=schema.scheduled_at,
            meeting_link=schema.meeting_link,
            notes=schema.notes,
            admin_id=admin.id,
        )
        formatted_time = schema.scheduled_at.strftime("%Y-%m-%d %H:%M")
        bg.add_task(
            ns.notify,
            recepients=[schema.user_id],
            title="Online Interview Scheduled",
            body=f"Your online interview has been scheduled for {formatted_time}.",
            type=NotificationType.SYSTEM_ALERT,
            data={
                "interview_id": interview.id,
                "scheduled_at": schema.scheduled_at.isoformat(),
                "meeting_link": schema.meeting_link,
            },
        )
        return BaseAPIResponse[InterviewResponse](
            data=InterviewResponse.model_validate(interview),
            detail="Online interview scheduled successfully.",
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to schedule online interview.",
        )


@router.put(
    "/interviews/{interview_id}/status",
    response_model=BaseAPIResponse[InterviewResponse],
    status_code=status.HTTP_200_OK,
)
async def update_interview_status(
    interview_id: str,
    schema: UpdateInterviewStatusRequest,
    bg: BackgroundTasks,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    interview_manager: InterviewManagerService = Depends(get_interview_manager_service),
    ns: NotificationService = Depends(get_notification_service),
):
    """Update interview status, marking pass/fail, updating user tier if passed, and sending background notification."""
    try:
        interview = await interview_manager.update_interview_status(
            interview_id=interview_id,
            interview_status=schema.status,
            notes=schema.notes,
            meeting_link=schema.meeting_link,
            scheduled_at=schema.scheduled_at,
            admin_id=admin.id,
        )
        if schema.status == InterviewStatus.PASSED:
            bg.add_task(
                ns.notify,
                recepients=[interview.user_id],
                title="Interview Passed!",
                body="Congratulations! You have passed your online interview and your tier level has been upgraded.",
                type=NotificationType.SYSTEM_ALERT,
                data={
                    "interview_id": interview.id,
                    "status": schema.status.value,
                },
            )
        else:
            bg.add_task(
                ns.notify,
                recepients=[interview.user_id],
                title="Interview Status Update",
                body=f"Your online interview status has been updated to {schema.status.value}.",
                type=NotificationType.SYSTEM_ALERT,
                data={
                    "interview_id": interview.id,
                    "status": schema.status.value,
                },
            )
        return BaseAPIResponse[InterviewResponse](
            data=InterviewResponse.model_validate(interview),
            detail=f"Interview status updated to {schema.status.value} successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update interview status.",
        )


@router.get(
    "/interviews",
    response_model=BaseAPIResponse[PaginatedData[InterviewResponse]],
    status_code=status.HTTP_200_OK,
    summary="List provider interviews with flexible filters and pagination",
)
async def list_interviews(
    user_id: Optional[str] = Query(None, description="Filter by provider user ID"),
    admin_id: Optional[str] = Query(None, description="Filter by scheduling admin ID"),
    interview_status: Optional[InterviewStatus] = Query(None, alias="status", description="Filter by interview status (SCHEDULED, COMPLETED, PASSED, FAILED, CANCELLED, RESCHEDULED)"),
    meeting_link: Optional[str] = Query(None, description="Search by meeting link URL"),
    notes: Optional[str] = Query(None, description="Search by admin interview notes"),
    search: Optional[str] = Query(None, description="Global search query across notes, meeting link, user_id, admin_id"),
    scheduled_from: Optional[datetime] = Query(None, description="Filter interviews scheduled on or after timestamp"),
    scheduled_to: Optional[datetime] = Query(None, description="Filter interviews scheduled on or before timestamp"),
    passed_from: Optional[datetime] = Query(None, description="Filter interviews passed on or after timestamp"),
    passed_to: Optional[datetime] = Query(None, description="Filter interviews passed on or before timestamp"),
    created_from: Optional[datetime] = Query(None, description="Filter interviews created on or after timestamp"),
    created_to: Optional[datetime] = Query(None, description="Filter interviews created on or before timestamp"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: str = Query("scheduled_at", description="Field to sort by (scheduled_at, created_at, passed_at, status)"),
    order: str = Query("desc", description="Sort order direction (asc or desc)"),
    admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """List provider interviews for admin review with inlined query, extensive filtering, and pagination."""
    try:
        stmt = select(ProviderInterview)

        if user_id:
            stmt = stmt.where(col(ProviderInterview.user_id) == user_id)
        if admin_id:
            stmt = stmt.where(col(ProviderInterview.admin_id) == admin_id)
        if interview_status:
            stmt = stmt.where(col(ProviderInterview.status) == interview_status)
        if meeting_link:
            stmt = stmt.where(col(ProviderInterview.meeting_link).ilike(f"%{meeting_link.strip()}%"))
        if notes:
            stmt = stmt.where(col(ProviderInterview.notes).ilike(f"%{notes.strip()}%"))

        if search:
            term = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    col(ProviderInterview.notes).ilike(term),
                    col(ProviderInterview.meeting_link).ilike(term),
                    col(ProviderInterview.user_id).ilike(term),
                    col(ProviderInterview.admin_id).ilike(term),
                )
            )

        if scheduled_from:
            stmt = stmt.where(col(ProviderInterview.scheduled_at) >= scheduled_from)
        if scheduled_to:
            stmt = stmt.where(col(ProviderInterview.scheduled_at) <= scheduled_to)
        if passed_from:
            stmt = stmt.where(col(ProviderInterview.passed_at) >= passed_from)
        if passed_to:
            stmt = stmt.where(col(ProviderInterview.passed_at) <= passed_to)
        if created_from:
            stmt = stmt.where(col(ProviderInterview.created_at) >= created_from)
        if created_to:
            stmt = stmt.where(col(ProviderInterview.created_at) <= created_to)

        valid_sort_fields = {"scheduled_at", "created_at", "passed_at", "status"}
        actual_sort = sort_by if sort_by in valid_sort_fields else "scheduled_at"
        sort_attr = getattr(ProviderInterview, actual_sort, ProviderInterview.scheduled_at)

        if order.lower() == "asc":
            stmt = stmt.order_by(col(sort_attr).asc())
        else:
            stmt = stmt.order_by(col(sort_attr).desc())

        all_interviews = (await session.exec(stmt)).all()
        total = len(all_interviews)

        offset = (page - 1) * per_page
        paged_interviews = all_interviews[offset : offset + per_page]

        items = [InterviewResponse.model_validate(i) for i in paged_interviews]
        paginated_data = PaginatedData[InterviewResponse](
            items=items,
            total=total,
            page=page,
            per_page=per_page,
        )
        return BaseAPIResponse[PaginatedData[InterviewResponse]](
            data=paginated_data,
            detail="Provider interviews fetched successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch provider interviews.",
        )


@router.get(
    "/guarantors",
    response_model=BaseAPIResponse[PaginatedData[GuarantorResponse]],
    status_code=status.HTTP_200_OK,
    summary="List provider guarantors with flexible filters and pagination",
)
async def list_guarantors(
    provider_id: Optional[str] = Query(None, description="Filter by provider user ID"),
    guarantor_id: Optional[str] = Query(None, description="Filter by guarantor record ID"),
    verification_status: Optional[VerificationStatus] = Query(None, alias="status", description="Filter by verification status (PENDING, PASSED, FAILED, UNDER_REVIEW)"),
    guarantor_name: Optional[str] = Query(None, description="Search by guarantor full name (case-insensitive substring)"),
    guarantor_phone: Optional[str] = Query(None, description="Search by guarantor phone number"),
    relationship: Optional[str] = Query(None, description="Filter by relationship (case-insensitive substring)"),
    search: Optional[str] = Query(None, description="Global search query across name, phone, relationship, and provider ID"),
    created_from: Optional[datetime] = Query(None, description="Filter submissions created on or after timestamp"),
    created_to: Optional[datetime] = Query(None, description="Filter submissions created on or before timestamp"),
    verified_from: Optional[datetime] = Query(None, description="Filter submissions verified on or after timestamp"),
    verified_to: Optional[datetime] = Query(None, description="Filter submissions verified on or before timestamp"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: str = Query("created_at", description="Field to sort by (created_at, verified_at, guarantor_name, status)"),
    order: str = Query("desc", description="Sort order direction (asc or desc)"),
    admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """List provider guarantors for admin review with inlined query, flexible optional exhaustive filters, and pagination."""
    try:
        stmt = select(ProviderGuarantor)

        if guarantor_id:
            stmt = stmt.where(col(ProviderGuarantor.id) == guarantor_id)
        if provider_id:
            stmt = stmt.where(col(ProviderGuarantor.provider_id) == provider_id)
        if verification_status:
            stmt = stmt.where(col(ProviderGuarantor.status) == verification_status)
        if guarantor_name:
            stmt = stmt.where(col(ProviderGuarantor.guarantor_name).ilike(f"%{guarantor_name.strip()}%"))
        if guarantor_phone:
            stmt = stmt.where(col(ProviderGuarantor.guarantor_phone).contains(guarantor_phone.strip()))
        if relationship:
            stmt = stmt.where(col(ProviderGuarantor.relationship).ilike(f"%{relationship.strip()}%"))

        if search:
            term = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    col(ProviderGuarantor.guarantor_name).ilike(term),
                    col(ProviderGuarantor.guarantor_phone).ilike(term),
                    col(ProviderGuarantor.relationship).ilike(term),
                    col(ProviderGuarantor.provider_id).ilike(term),
                )
            )

        if created_from:
            stmt = stmt.where(col(ProviderGuarantor.created_at) >= created_from)
        if created_to:
            stmt = stmt.where(col(ProviderGuarantor.created_at) <= created_to)
        if verified_from:
            stmt = stmt.where(col(ProviderGuarantor.verified_at) >= verified_from)
        if verified_to:
            stmt = stmt.where(col(ProviderGuarantor.verified_at) <= verified_to)

        valid_sort_fields = {"created_at", "verified_at", "guarantor_name", "status"}
        actual_sort = sort_by if sort_by in valid_sort_fields else "created_at"
        sort_attr = getattr(ProviderGuarantor, actual_sort, ProviderGuarantor.created_at)

        if order.lower() == "asc":
            stmt = stmt.order_by(col(sort_attr).asc())
        else:
            stmt = stmt.order_by(col(sort_attr).desc())

        all_guarantors = (await session.exec(stmt)).all()
        total = len(all_guarantors)

        offset = (page - 1) * per_page
        paged_guarantors = all_guarantors[offset : offset + per_page]

        items = [GuarantorResponse.model_validate(g) for g in paged_guarantors]
        paginated_data = PaginatedData[GuarantorResponse](
            items=items,
            total=total,
            page=page,
            per_page=per_page,
        )
        return BaseAPIResponse[PaginatedData[GuarantorResponse]](
            data=paginated_data,
            detail="Guarantors retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve guarantors.",
        )


@router.get(
    "/kyc-documents",
    response_model=BaseAPIResponse[PaginatedData[KYCDocumentResponse]],
    status_code=status.HTTP_200_OK,
    summary="List provider KYC documents with flexible filters and pagination",
)
async def list_kyc_documents(
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    document_id: Optional[str] = Query(None, description="Filter by KYC document ID"),
    provider_profile_id: Optional[str] = Query(None, description="Filter by provider profile ID"),
    id_type: Optional[str] = Query(None, description="Filter by ID document type (e.g., NIN, BVN, PASSPORT)"),
    id_number: Optional[str] = Query(None, description="Filter by ID document number"),
    kyc_status: Optional[KYCStatus] = Query(None, alias="status", description="Filter by KYC status (SUBMITTED, UNDER_REVIEW, VERIFIED, FAILED, PENDING_SUBMISSION)"),
    attempt_number: Optional[int] = Query(None, description="Filter by submission attempt number"),
    search: Optional[str] = Query(None, description="Global search query across id_number, id_type, rejection_reason, user_id"),
    submitted_from: Optional[datetime] = Query(None, description="Filter documents submitted on or after timestamp"),
    submitted_to: Optional[datetime] = Query(None, description="Filter documents submitted on or before timestamp"),
    reviewed_from: Optional[datetime] = Query(None, description="Filter documents reviewed on or after timestamp"),
    reviewed_to: Optional[datetime] = Query(None, description="Filter documents reviewed on or before timestamp"),
    created_from: Optional[datetime] = Query(None, description="Filter documents created on or after timestamp"),
    created_to: Optional[datetime] = Query(None, description="Filter documents created on or before timestamp"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: str = Query("submitted_at", description="Field to sort by (submitted_at, reviewed_at, created_at, status, id_type)"),
    order: str = Query("desc", description="Sort order direction (asc or desc)"),
    admin: AdminUser = Depends(GetCurrentAdmin()),
    session: AsyncSession = Depends(get_session),
):
    """List provider KYC document submissions for admin review with inlined query, extensive filtering, and pagination."""
    try:
        stmt = select(KYCDocument)

        if document_id:
            stmt = stmt.where(col(KYCDocument.id) == document_id)
        if user_id:
            stmt = stmt.where(col(KYCDocument.user_id) == user_id)
        if provider_profile_id:
            stmt = stmt.where(col(KYCDocument.provider_profile_id) == provider_profile_id)
        if id_type:
            stmt = stmt.where(col(KYCDocument.id_type).ilike(f"%{id_type.strip()}%"))
        if id_number:
            stmt = stmt.where(col(KYCDocument.id_number).contains(id_number.strip()))
        if kyc_status:
            stmt = stmt.where(col(KYCDocument.status) == kyc_status)
        if attempt_number is not None:
            stmt = stmt.where(col(KYCDocument.attempt_number) == attempt_number)

        if search:
            term = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    col(KYCDocument.id_number).ilike(term),
                    col(KYCDocument.id_type).ilike(term),
                    col(KYCDocument.rejection_reason).ilike(term),
                    col(KYCDocument.user_id).ilike(term),
                )
            )

        if submitted_from:
            stmt = stmt.where(col(KYCDocument.submitted_at) >= submitted_from)
        if submitted_to:
            stmt = stmt.where(col(KYCDocument.submitted_at) <= submitted_to)
        if reviewed_from:
            stmt = stmt.where(col(KYCDocument.reviewed_at) >= reviewed_from)
        if reviewed_to:
            stmt = stmt.where(col(KYCDocument.reviewed_at) <= reviewed_to)
        if created_from:
            stmt = stmt.where(col(KYCDocument.created_at) >= created_from)
        if created_to:
            stmt = stmt.where(col(KYCDocument.created_at) <= created_to)

        valid_sort_fields = {"submitted_at", "reviewed_at", "created_at", "status", "id_type"}
        actual_sort = sort_by if sort_by in valid_sort_fields else "submitted_at"
        sort_attr = getattr(KYCDocument, actual_sort, KYCDocument.submitted_at)

        if order.lower() == "asc":
            stmt = stmt.order_by(col(sort_attr).asc())
        else:
            stmt = stmt.order_by(col(sort_attr).desc())

        all_documents = (await session.exec(stmt)).all()
        total = len(all_documents)

        offset = (page - 1) * per_page
        paged_documents = all_documents[offset : offset + per_page]

        items = [KYCDocumentResponse.model_validate(doc) for doc in paged_documents]
        paginated_data = PaginatedData[KYCDocumentResponse](
            items=items,
            total=total,
            page=page,
            per_page=per_page,
        )
        return BaseAPIResponse[PaginatedData[KYCDocumentResponse]](
            data=paginated_data,
            detail="KYC documents retrieved successfully.",
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve KYC documents.",
        )
