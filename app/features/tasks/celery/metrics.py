from app.core.utils.timer import Timer
from app.core.services.logger_service import get_logger_service_manual
from datetime import timedelta
from typing import Optional

from celery import shared_task
from sqlmodel import select

from app.core.database import async_session_maker
from app.core.logging import logger
from app.core.models.services import Service, ServiceCategory
from app.core.models.tasks import (
    DispatchAttemptStatus,
    Task,
    TaskAssignment,
    TaskAssignmentStatus,
    TaskDispatchAttempt,
)
from app.core.models.users import ProviderProfile
from app.core.repository import Repository
from app.core.utils.celery import run_async
from app.core.utils.datetime_helper import lagos_now


@shared_task(name="tasks.sync_provider_metrics")
def sync_provider_metrics(provider_id: str):
    """Syncs rolling 30-day acceptance_rate_30d and completion_rate_30d for a provider."""
    logger.info(f"Syncing 30-day performance metrics for provider {provider_id}")
    return run_async(_sync_provider_metrics_async(provider_id))


@shared_task(name="tasks.sync_service_duration_metrics")
def sync_service_metrics(
    service_id: Optional[str] = None, category_id: Optional[str] = None
):
    """Syncs average task duration (in minutes) for a service and/or category based on completed task assignments."""
    logger.info(
        f"Syncing average task duration metrics for service_id={service_id}, category_id={category_id}"
    )
    return run_async(
        _sync_service_metrics_async(
            service_id=service_id, category_id=category_id
        )
    )


async def _sync_provider_metrics_async(provider_id: str):
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            profile_repo = Repository(ProviderProfile, session)
            attempt_repo = Repository(TaskDispatchAttempt, session)
            assignment_repo = Repository(TaskAssignment, session)

            cutoff = lagos_now() - timedelta(days=30)

            # 1. Calculate 30-day acceptance rate
            stmt_pings = select(TaskDispatchAttempt).where(
                TaskDispatchAttempt.provider_id == provider_id,
                TaskDispatchAttempt.pinged_at >= cutoff,  # type: ignore
            )
            res_pings = await attempt_repo.execute(stmt_pings)
            pings = list(res_pings.all())

            total_pings = len(pings)
            accepted_pings = sum(1 for p in pings if p.status == DispatchAttemptStatus.ACCEPTED)
            acceptance_rate = (accepted_pings / total_pings * 100.0) if total_pings > 0 else 100.0

            # 2. Calculate 30-day completion rate
            stmt_assignments = select(TaskAssignment).where(
                TaskAssignment.provider_id == provider_id,
                TaskAssignment.assigned_at >= cutoff,
            )
            res_assignments = await assignment_repo.execute(stmt_assignments)
            assignments = list(res_assignments.all())

            total_assignments = len(assignments)
            completed_assignments = sum(
                1 for a in assignments if a.status == TaskAssignmentStatus.COMPLETED
            )
            completion_rate = (
                (completed_assignments / total_assignments * 100.0)
                if total_assignments > 0
                else 100.0
            )

            # 3. Update ProviderProfile
            stmt_prof = select(ProviderProfile).where(ProviderProfile.user_id == provider_id)
            res_prof = await profile_repo.execute(stmt_prof)
            profile: Optional[ProviderProfile] = res_prof.scalar_one_or_none()
            if profile:
                profile.acceptance_rate_30d = round(acceptance_rate, 2)
                profile.completion_rate_30d = round(completion_rate, 2)
                await profile_repo.add(profile)
                logger.info(
                    f"Updated provider {provider_id} metrics: acceptance={acceptance_rate:.1f}%, completion={completion_rate:.1f}%"
                )


            await system_logger.metric('sync_provider_metrics', timer.stop(), source='celery.sync_provider_metrics')
        except Exception as e:
            await system_logger.error(f'sync_provider_metrics Failed: {str(e)}', source='celery.sync_provider_metrics')
            raise e
async def _sync_single_service_duration(
    service_id: str,
    service_repo: Repository[Service],
    assignment_repo: Repository[TaskAssignment],
) -> Optional[float]:
    stmt = (
        select(TaskAssignment)
        # pyrefly: ignore [bad-argument-type]
        .join(Task, TaskAssignment.task_id == Task.id)
        .where(
            Task.service_id == service_id,
            TaskAssignment.started_at != None,  # noqa: E711
            TaskAssignment.completed_at != None,  # noqa: E711
            TaskAssignment.status == TaskAssignmentStatus.COMPLETED,
        )
    )
    res = await assignment_repo.execute(stmt)
    assignments = list(res.all())

    durations = [
        (a.completed_at - a.started_at).total_seconds() / 60.0
        for a in assignments
        if a.completed_at and a.started_at and a.completed_at >= a.started_at
    ]

    if not durations:
        return None

    avg_duration = sum(durations) / len(durations)
    stmt_srv = select(Service).where(Service.id == service_id)
    res_srv = await service_repo.execute(stmt_srv)
    service: Optional[Service] = res_srv.scalar_one_or_none()
    if service:
        service.default_duration_min = round(avg_duration)
        await service_repo.add(service)
        logger.info(
            f"Updated service {service_id} average duration: {service.default_duration_min} min (from {len(durations)} tasks)"
        )
    return avg_duration


async def _sync_single_category_duration(
    category_id: str,
    category_repo: Repository[ServiceCategory],
    assignment_repo: Repository[TaskAssignment],
) -> Optional[float]:
    stmt = (
        select(TaskAssignment)
        # pyrefly: ignore [bad-argument-type]
        .join(Task, TaskAssignment.task_id == Task.id)
        # pyrefly: ignore [bad-argument-type]
        .outerjoin(Service, Task.service_id == Service.id)
        .where(
            (Task.category_id == category_id) | (Service.category_id == category_id),
            TaskAssignment.started_at != None,  # noqa: E711
            TaskAssignment.completed_at != None,  # noqa: E711
            TaskAssignment.status == TaskAssignmentStatus.COMPLETED,
        )
    )
    res = await assignment_repo.execute(stmt)
    assignments = list(res.all())

    durations = [
        (a.completed_at - a.started_at).total_seconds() / 60.0
        for a in assignments
        if a.completed_at and a.started_at and a.completed_at >= a.started_at
    ]

    if not durations:
        return None

    avg_duration = sum(durations) / len(durations)
    stmt_cat = select(ServiceCategory).where(ServiceCategory.id == category_id)
    res_cat = await category_repo.execute(stmt_cat)
    category: Optional[ServiceCategory] = res_cat.scalar_one_or_none()
    if category:
        category.default_duration_min = round(avg_duration)
        await category_repo.add(category)
        logger.info(
            f"Updated category {category_id} average duration: {category.default_duration_min} min (from {len(durations)} tasks)"
        )
    return avg_duration


async def _sync_service_metrics_async(
    service_id: Optional[str] = None, category_id: Optional[str] = None
):
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            service_repo = Repository(Service, session)
            category_repo = Repository(ServiceCategory, session)
            assignment_repo = Repository(TaskAssignment, session)

            if service_id:
                await _sync_single_service_duration(
                    service_id, service_repo, assignment_repo
                )
                if not category_id:
                    stmt_srv = select(Service).where(Service.id == service_id)
                    res_srv = await service_repo.execute(stmt_srv)
                    srv: Optional[Service] = res_srv.scalar_one_or_none()
                    if srv and srv.category_id:
                        category_id = srv.category_id

            if category_id:
                await _sync_single_category_duration(
                    category_id, category_repo, assignment_repo
                )

            if not service_id and not category_id:
                res_services = await service_repo.execute(select(Service))
                services = list(res_services.all())
                for s in services:
                    await _sync_single_service_duration(
                        s.id, service_repo, assignment_repo
                    )

                res_categories = await category_repo.execute(select(ServiceCategory))
                categories = list(res_categories.all())
                for c in categories:
                    await _sync_single_category_duration(
                        c.id, category_repo, assignment_repo
                    )

            await system_logger.metric('sync_service_metrics', timer.stop(), source='celery.sync_service_metrics')
        except Exception as e:
            await system_logger.error(f'sync_service_metrics Failed: {str(e)}', source='celery.sync_service_metrics')
            raise e
