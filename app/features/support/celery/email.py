from typing import Optional
from celery import shared_task
from sqlmodel import col, select

from app.core.celery_database import celery_session_factory
from app.core.logging import logger
from app.core.models.support import CaseStatus, SupportCase
from app.core.utils.celery import run_async
from app.core.utils.datetime_helper import lagos_now


async def _send_support_email_async(
    to_email: str,
    subject: str,
    body: str,
    case_number: str,
    reply_token: Optional[str] = None,
) -> bool:
    """Helper to send support email via configured EmailService."""
    from app.core.services import email_service

    formatted_subject = f"[{case_number}] {subject}"
    formatted_body = (
        f"{body}\n\n"
        f"---\n"
        f"Case Number: {case_number}\n"
        f"Reply Token: {reply_token or ''}\n"
    )
    try:
        await email_service.send_email(
            to_emails=to_email,
            subject=formatted_subject,
            body=formatted_body,
        )
        logger.info(f"Support email sent to {to_email} for case {case_number}")
        return True
    except Exception as e:
        logger.error(f"Failed to send support email for case {case_number}: {e}")
        return False


async def _check_support_sla_async() -> int:
    """Scans active support cases for SLA breaches (first response or resolution overdue)."""
    now = lagos_now()
    breached_count = 0
    async with celery_session_factory() as session:
        stmt = select(SupportCase).where(
            col(SupportCase.status).in_([CaseStatus.OPEN, CaseStatus.IN_PROGRESS, CaseStatus.WAITING_FOR_INTERNAL])
        )
        res = await session.exec(stmt)
        cases = res.all()

        for c in cases:
            is_first_response_breached = (
                c.first_response_due_at is not None
                and c.first_responded_at is None
                and c.first_response_due_at < now
            )
            is_resolution_breached = (
                c.resolution_due_at is not None
                and c.resolved_at is None
                and c.resolution_due_at < now
            )

            if is_first_response_breached or is_resolution_breached:
                breached_count += 1
                logger.warning(
                    f"SLA Breach detected for case {c.case_number}: "
                    f"first_response_overdue={is_first_response_breached}, "
                    f"resolution_overdue={is_resolution_breached}"
                )

    return breached_count


@shared_task(name="support.send_support_email")
def send_support_email_task(
    to_email: str,
    subject: str,
    body: str,
    case_number: str,
    reply_token: Optional[str] = None,
):
    """Celery task to send support email notification."""
    return run_async(
        _send_support_email_async(
            to_email=to_email,
            subject=subject,
            body=body,
            case_number=case_number,
            reply_token=reply_token,
        )
    )


@shared_task(name="support.check_support_sla")
def check_support_sla_task():
    """Celery Beat task to check for support SLA breaches."""
    return run_async(_check_support_sla_async())
