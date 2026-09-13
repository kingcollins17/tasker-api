from typing import List, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, status, HTTPException
from app.core.api_response import BaseAPIResponse
from app.core.deps import GetCurrentAdmin
from app.core.error_handler import AppErrorHandler
from app.core.models.admins import AdminUser
from app.core.models.notifications import NotificationType
from app.core.models.vetting import InterviewStatus
from app.features.notifications.notification_service import (
    NotificationService,
    get_notification_service,
)
from app.features.users.services.kyc_service import KYCService, get_kyc_service
from app.features.vetting.schemas import (
    ApproveGuarantorRequest,
    RejectGuarantorRequest,
    GuarantorResponse,
    ApproveKYCRequest,
    RejectKYCRequest,
    ScheduleInterviewRequest,
    UpdateInterviewStatusRequest,
    InterviewResponse,
)
from app.features.vetting.vetting_service import VettingService, get_vetting_service
from app.features.vetting.interview_manager_service import (
    InterviewManagerService,
    get_interview_manager_service,
)

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
    response_model=BaseAPIResponse[List[InterviewResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_interviews(
    interview_status: Optional[InterviewStatus] = None,
    limit: int = 50,
    offset: int = 0,
    admin: AdminUser = Depends(GetCurrentAdmin()),
    interview_manager: InterviewManagerService = Depends(get_interview_manager_service),
):
    """List provider interviews for admin review."""
    try:
        interviews = await interview_manager.list_interviews(
            interview_status=interview_status,
            limit=limit,
            offset=offset,
        )
        return BaseAPIResponse[List[InterviewResponse]](
            data=[InterviewResponse.model_validate(i) for i in interviews],
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
