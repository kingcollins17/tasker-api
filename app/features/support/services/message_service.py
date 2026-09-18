from typing import List, Optional, Tuple

from fastapi import Depends
from sqlmodel import col, select, update, or_
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.models.support import (
    CaseAttachment,
    CaseEvent,
    CaseEventType,
    CaseMessage,
    CaseStatus,
    MessageChannel,
    MessageSenderType,
    MessageVisibility,
    SupportCase,
)
from app.core.utils.datetime_helper import lagos_now


class CaseMessageService:
    """Service handling message creation, internal notes, idempotency checks, and email webhook ingestion."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def send_message(
        self,
        case_id: str,
        sender_id: str,
        sender_type: MessageSenderType,
        body: str,
        channel: MessageChannel = MessageChannel.IN_APP,
        visibility: MessageVisibility = MessageVisibility.PUBLIC,
        email_message_id: Optional[str] = None,
        status_update: Optional[CaseStatus] = None,
        attachment_ids: Optional[List[str]] = None,
    ) -> Tuple[Optional[CaseMessage], Optional[SupportCase]]:
        # Idempotency check if email_message_id is provided
        if email_message_id:
            stmt_dup = select(CaseMessage).where(col(CaseMessage.email_message_id) == email_message_id)
            existing = await self.session.exec(stmt_dup)
            if existing.first():
                return None, None

        # Fetch case
        stmt_case = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        res_case = await self.session.exec(stmt_case)
        case = res_case.first()
        if not case:
            return None, None

        now = lagos_now()
        message = CaseMessage(
            case_id=case.id,
            sender_type=sender_type,
            sender_id=sender_id,
            channel=channel,
            visibility=visibility,
            body=body,
            email_message_id=email_message_id,
            created_at=now,
        )
        self.session.add(message)
        await self.session.flush()

        # Link attachments if provided
        if attachment_ids:
            stmt_att = (
                update(CaseAttachment)
                .where(col(CaseAttachment.id).in_(attachment_ids))
                .values(message_id=message.id)
            )
            await self.session.exec(stmt_att)

        # Status & timestamp updates
        if status_update:
            case.status = status_update
        elif sender_type == MessageSenderType.AGENT:
            if visibility == MessageVisibility.PUBLIC and not case.first_responded_at:
                case.first_responded_at = now
            if case.status == CaseStatus.WAITING_FOR_INTERNAL:
                case.status = CaseStatus.IN_PROGRESS
        elif sender_type in (MessageSenderType.CUSTOMER, MessageSenderType.PROVIDER):
            if case.status in (CaseStatus.WAITING_FOR_CUSTOMER, CaseStatus.WAITING_FOR_PROVIDER):
                case.status = CaseStatus.IN_PROGRESS

        case.updated_at = now
        self.session.add(case)

        # Log event
        event_type = (
            CaseEventType.INTERNAL_NOTE_ADDED
            if visibility == MessageVisibility.INTERNAL
            else CaseEventType.MESSAGE_SENT
        )
        event = CaseEvent(
            case_id=case.id,
            event_type=event_type,
            actor_type=sender_type.value,
            actor_id=sender_id,
            event_metadata={
                "message_id": message.id,
                "channel": channel.value,
                "visibility": visibility.value,
                "attachment_count": len(attachment_ids) if attachment_ids else 0,
            },
            created_at=now,
        )
        self.session.add(event)

        await self.session.commit()
        await self.session.refresh(message)
        await self.session.refresh(case)
        return message, case

    async def add_internal_note(
        self,
        case_id: str,
        agent_id: str,
        body: str,
    ) -> Tuple[Optional[CaseMessage], Optional[SupportCase]]:
        return await self.send_message(
            case_id=case_id,
            sender_id=agent_id,
            sender_type=MessageSenderType.AGENT,
            body=body,
            channel=MessageChannel.IN_APP,
            visibility=MessageVisibility.INTERNAL,
        )

    async def process_email_webhook(
        self,
        reply_token_or_case_number: str,
        sender_email: str,
        body: str,
        email_message_id: str,
    ) -> Optional[CaseMessage]:
        stmt = select(SupportCase).where(
            or_(
                col(SupportCase.reply_token) == reply_token_or_case_number,
                col(SupportCase.case_number) == reply_token_or_case_number,
            )
        )
        res = await self.session.exec(stmt)
        case = res.first()
        if not case:
            return None

        msg, _ = await self.send_message(
            case_id=case.id,
            sender_id=sender_email,
            sender_type=MessageSenderType.CUSTOMER,
            body=body,
            channel=MessageChannel.EMAIL,
            visibility=MessageVisibility.PUBLIC,
            email_message_id=email_message_id,
        )
        return msg


def get_case_message_service(session: AsyncSession = Depends(get_session)) -> CaseMessageService:
    return CaseMessageService(session)
