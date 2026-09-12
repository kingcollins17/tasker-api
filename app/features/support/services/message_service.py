from typing import List, Optional, Tuple

from sqlmodel import col, select, or_
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.models.support import (
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

        # Status & timestamp updates
        if sender_type == MessageSenderType.AGENT:
            if visibility == MessageVisibility.PUBLIC and not case.first_responded_at:
                case.first_responded_at = now
            if case.status == CaseStatus.WAITING_FOR_INTERNAL:
                case.status = CaseStatus.IN_PROGRESS
        elif sender_type in (MessageSenderType.CUSTOMER, MessageSenderType.PROVIDER):
            if case.status == CaseStatus.WAITING_FOR_USER:
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

    async def get_messages(
        self,
        case_id: str,
        is_admin: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> List[CaseMessage]:
        stmt_case = select(SupportCase).where(
            or_(
                col(SupportCase.id) == case_id,
                col(SupportCase.case_number) == case_id,
            )
        )
        res_case = await self.session.exec(stmt_case)
        case = res_case.first()
        if not case:
            return []

        stmt = select(CaseMessage).where(col(CaseMessage.case_id) == case.id)
        if not is_admin:
            stmt = stmt.where(col(CaseMessage.visibility) == MessageVisibility.PUBLIC)

        stmt = stmt.order_by(col(CaseMessage.created_at).asc()).limit(limit).offset(offset)
        res = await self.session.exec(stmt)
        return res.all()

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
