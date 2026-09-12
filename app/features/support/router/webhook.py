from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.api_response import BaseAPIResponse
from app.core.database import get_session
from app.core.error_handler import AppErrorHandler
from app.features.support.schemas import CaseMessageResponse
from app.features.support.services.message_service import CaseMessageService

router = APIRouter()


class EmailWebhookPayload(BaseModel):
    reply_token_or_case_number: str = Field(..., description="Case number or reply token extracted from email header/subject")
    sender_email: str = Field(..., description="Email address of sender")
    body: str = Field(..., description="Email body content")
    email_message_id: str = Field(..., description="Unique email Message-ID header for idempotency")


@router.post("/email-webhook", response_model=BaseAPIResponse[Optional[CaseMessageResponse]])
async def incoming_email_webhook(
    payload: EmailWebhookPayload,
    session: AsyncSession = Depends(get_session),
):
    try:
        service = CaseMessageService(session)
        msg = await service.process_email_webhook(
            reply_token_or_case_number=payload.reply_token_or_case_number,
            sender_email=payload.sender_email,
            body=payload.body,
            email_message_id=payload.email_message_id,
        )
        if not msg:
            return BaseAPIResponse.success_response(
                data=None,
                message="Email message ignored or case not found (or duplicate email_message_id)",
            )
        return BaseAPIResponse.success_response(
            data=CaseMessageResponse.model_validate(msg),
            message="Incoming email processed successfully",
        )
    except HTTPException as e:
        raise e
    except Exception as error:
        AppErrorHandler.handleError(error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process incoming email webhook",
        )
