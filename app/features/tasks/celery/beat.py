"""Celery Beat tasks for task dispatches.

This module owns scheduled periodic Celery Beat timers that trigger background worker tasks.
Beat tasks strictly act as alarm signals and perform no database or business logic.
"""

from celery import shared_task

from app.core.logging import logger
from app.features.tasks.celery.dispatch import (
    process_due_dispatches_task,
    recover_stale_dispatches_task,
)


@shared_task(name="tasks.process_due_dispatches_beat")
def process_due_dispatches_beat():
    """Celery Beat periodic timer: triggers process_due_dispatches_task worker task."""
    logger.info("process_due_dispatches_beat: triggering process_due_dispatches_task worker task")
    # pyrefly: ignore [not-callable]
    process_due_dispatches_task.delay()


@shared_task(name="tasks.recover_stale_dispatches_beat")
def recover_stale_dispatches_beat():
    """Celery Beat periodic timer: triggers recover_stale_dispatches_task worker task."""
    logger.info("recover_stale_dispatches_beat: triggering recover_stale_dispatches_task worker task")
    # pyrefly: ignore [not-callable]
    recover_stale_dispatches_task.delay()
