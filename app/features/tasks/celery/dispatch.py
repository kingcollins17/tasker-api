"""Celery dispatch tasks.

This module owns background dispatch execution tasks, Celery Beat task claim schedulers,
and stale dispatch claim recovery jobs.
"""

from app.core.models import ProviderProfile
from app.core.models import TaskAssignment
from datetime import timedelta
from typing import List, Optional

from celery import shared_task
from sqlalchemy import update
from sqlmodel import col, select

from app.core.celery_database import celery_session_factory
from app.core.logging import logger
from app.core.models.tasks import (
    DispatchSession,
    DispatchSessionStatus,
    Task,
    TaskDispatchAttempt,
    TaskDispatchStatus,
    TaskEventHistory,
    TaskStatus,
)
from app.core.repository import Repository
from app.core.services.dispatch_policy import DispatchPolicy
from app.core.services.matching_engine import MatchingEngine
from app.core.utils.celery import run_async
from app.core.utils.datetime_helper import lagos_now
from app.features.credibility.credibility_service import get_credibility_service_manual
from app.features.notifications.notification_service import get_notification_service_manual
async def _process_auto_retry_async(task_id: str) -> Optional[str]:
    """Executes auto retry cycle for task_id using DispatchService."""
    from app.features.tasks.dispatch_service import DispatchService

    async with celery_session_factory() as session:
        dispatch_service = DispatchService(
            session=session,
            system_logger=None,  # type: ignore
            notification_service=get_notification_service_manual(session),
            credibility_service=get_credibility_service_manual(session),
        )
        ds = await dispatch_service.auto_retry(task_id)
        return ds.id if ds else None



async def _execute_matching_engine_async(
    session_id: str,
    exclude_previous_sessions: bool = True,
    excluded_provider_ids: Optional[List[str]] = None,
) -> bool:
    """Runs a single step of ephemeral MatchingEngine for session_id."""
    from app.features.tasks.dispatch_service import DispatchService

    async with celery_session_factory() as session:
        engine = MatchingEngine(
            session_id=session_id,
            session=session,
            exclude_previous_sessions=exclude_previous_sessions,
            excluded_provider_ids=excluded_provider_ids,
        )
        result = await engine.run()
        if not result.matched:
            ds = await session.get(DispatchSession, session_id)
            if ds:
                dispatch_service = DispatchService(
                    session=session,
                    system_logger=None,  # type: ignore
                    notification_service=get_notification_service_manual(session),
                    credibility_service=get_credibility_service_manual(session),
                )
                await dispatch_service.handle_no_match(
                    task_id=ds.task_id, session_id=session_id, reason=result.reason
                )
        return result.matched


# ---------------------------------------------------------------------------
# Public Celery tasks
# ---------------------------------------------------------------------------


async def _process_due_dispatches_async(batch_size: int = 500, max_batches: int = 20) -> int:
    """Claims due retry tasks using FOR UPDATE SKIP LOCKED and queues worker execution tasks."""
    total_processed = 0
    now = lagos_now()

    for _ in range(max_batches):
        async with celery_session_factory() as session:
            task_repo = Repository(Task, session)

            stmt_due = (
                select(Task.id)
                .where(
                    Task.dispatch_status == TaskDispatchStatus.RETRY_SCHEDULED,
                    col(Task.next_dispatch_at) <= now,
                )
                .order_by(col(Task.next_dispatch_at))
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            res_due = await task_repo.execute(stmt_due)
            raw_ids = res_due.all()
            due_task_ids = [
                (row[0] if isinstance(row, (tuple, list)) else row)
                for row in raw_ids
                if (row[0] if isinstance(row, (tuple, list)) else row) is not None
            ]

            if not due_task_ids:
                break

            stmt_claim = (
                update(Task)
                .where(col(Task.id).in_(due_task_ids))
                .values(
                    dispatch_status=TaskDispatchStatus.DISPATCHING,
                    dispatch_claimed_at=now,
                )
            )
            await task_repo.execute(stmt_claim)
            await session.commit()

            for tid in due_task_ids:
                # pyrefly: ignore [not-callable]
                process_auto_retry.delay(tid)

            total_processed += len(due_task_ids)

            if len(due_task_ids) < batch_size:
                break

    return total_processed


async def _recover_stale_dispatches_async(timeout_seconds: Optional[int] = None) -> int:
    """Recovers tasks stuck in DISPATCHING status longer than timeout_seconds."""
    if timeout_seconds is None:
        timeout_seconds = DispatchPolicy.STALE_CLAIM_TIMEOUT_SECONDS
    async with celery_session_factory() as session:
        task_repo = Repository(Task, session)
        stale_threshold = lagos_now() - timedelta(seconds=timeout_seconds)

        stmt_stale = (
            update(Task)
            .where(
                col(Task.dispatch_status) == TaskDispatchStatus.DISPATCHING,
                col(Task.dispatch_claimed_at) <= stale_threshold,
            )
            .values(
                dispatch_status=TaskDispatchStatus.RETRY_SCHEDULED,
                next_dispatch_at=lagos_now(),
            )
        )
        res = await task_repo.execute(stmt_stale)
        await session.commit()
        return res.rowcount or 0


@shared_task(name="tasks.process_auto_retry")
def process_auto_retry(task_id: str):
    """Celery worker entrypoint to process an automatic task dispatch retry."""
    logger.info(f"process_auto_retry: processing retry for task {task_id}")
    return run_async(_process_auto_retry_async(task_id=task_id))


@shared_task(name="tasks.process_due_dispatches_task")
def process_due_dispatches_task():
    """Celery worker task to claim due retry tasks in batches."""
    logger.info("process_due_dispatches_task: processing due task dispatches")
    return run_async(_process_due_dispatches_async())


@shared_task(name="tasks.recover_stale_dispatches_task")
def recover_stale_dispatches_task():
    """Celery worker task to recover stuck stale dispatch claims."""
    logger.info("recover_stale_dispatches_task: recovering stale dispatch claims")
    return run_async(_recover_stale_dispatches_async())


@shared_task(name="tasks.execute_matching_engine")
def execute_matching_engine(
    session_id: str,
    exclude_previous_sessions: bool = False,
    excluded_provider_ids: Optional[List[str]] = None,
):
    """Celery worker entrypoint to run one step of ephemeral MatchingEngine."""
    logger.info(f"execute_matching_engine: running for session {session_id}")
    return run_async(
        _execute_matching_engine_async(
            session_id=session_id,
            exclude_previous_sessions=exclude_previous_sessions,
            excluded_provider_ids=excluded_provider_ids,
        )
    )
