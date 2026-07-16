import asyncio
from typing import List
from celery import shared_task, chain
from sqlmodel import select, col
from sqlalchemy import func

from app.core.database import async_session_maker
from app.core.logging import logger
from app.core.models.tasks import Task, TaskLocation
from app.core.models.users import User, ProviderProfile, UserLocation
from app.core.models.services import ProviderServiceLink
from app.core.models.notifications import (
    Notification,
    NotificationType,
    NotificationRecipient,
    NotificationPriority,
)
from app.core.repository import Repository
from app.features.notifications.tasks import process_notification
from app.core.utils.celery import run_async
from app.core.queries.task_queries import TaskQueries


@shared_task(name="tasks.match_providers_for_task")
def match_providers_for_task(task_id: str, radius_km: float = 50.0) -> List[str]:
    """Finds providers matching the task criteria and returns their IDs."""
    logger.info(f"Matching providers for task {task_id} within {radius_km}km")
    return run_async(_match_providers_for_task_async(task_id, radius_km))


async def _match_providers_for_task_async(task_id: str, radius_km: float) -> List[str]:
    async with async_session_maker() as session:
        task_repo = Repository(Task, session)
        task = await task_repo.get(task_id)
        if not task:
            logger.warning(f"Task {task_id} not found.")
            return []

        # Get task location
        stmt_loc = (
            select(TaskLocation).where(col(TaskLocation.task_id) == task_id).limit(1)
        )
        res_loc = await session.exec(stmt_loc)
        task_loc = res_loc.one_or_none()

        if not task_loc or not task_loc.geography_point:
            logger.warning(f"Task {task_id} has no location data.")
            return []

        distance_m = radius_km * 1000

        # Query providers
        stmt = TaskQueries.get_providers_near_task_query(
            task, task_loc, radius_km, select_ids_only=True
        )

        res = await session.exec(stmt)
        provider_ids = list(res.all())

        logger.info(f"Found {len(provider_ids)} matching providers for task {task_id}")
        return provider_ids


@shared_task(name="tasks.notify_providers_of_task")
def notify_providers_of_task(provider_ids: List[str], task_id: str):
    """Creates notifications for the matched providers and dispatches them."""
    if not provider_ids:
        logger.info(f"No providers to notify for task {task_id}")
        return
    logger.info(f"Notifying {len(provider_ids)} providers for task {task_id}")
    run_async(_notify_providers_of_task_async(provider_ids, task_id))


async def _notify_providers_of_task_async(provider_ids: List[str], task_id: str):
    async with async_session_maker() as session:
        task_repo = Repository(Task, session)
        task = await task_repo.get(task_id)
        if not task:
            return

        notification = Notification(
            type=NotificationType.PROMOTION,
            title="New Task Available",
            body=f"A new task '{task.title}' is available in your area.",
            priority=NotificationPriority.HIGH,
            data={"task_id": task_id},
        )
        session.add(notification)
        await session.flush()

        recipients = [
            NotificationRecipient(notification_id=notification.id, recipient_id=pid)
            for pid in provider_ids
        ]
        session.add_all(recipients)
        await session.commit()

        # pyrefly: ignore [not-callable]
        process_notification.delay(notification.id)


@shared_task(name="tasks.process_new_task_workflow")
def process_new_task_workflow(task_id: str):
    """Workflow: match providers -> notify them."""
    logger.info(f"Starting new task workflow for task {task_id}")
    # pyright: ignore[reportGeneralTypeIssues, reportCallIssue]
    workflow = chain(
        # pyrefly: ignore [not-callable]
        match_providers_for_task.s(task_id),
        # pyrefly: ignore [not-callable]
        notify_providers_of_task.s(task_id),
    )
    workflow.apply_async()
