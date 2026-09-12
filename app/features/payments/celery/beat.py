"""Celery Beat tasks for payments and transfers.

This module owns scheduled periodic Celery Beat timers that trigger background worker tasks.
Beat tasks strictly act as alarm signals and perform no database or business logic.
"""

from celery import shared_task

from app.core.logging import logger
from app.features.payments.celery.transfer_tasks import (
    reconcile_processing_transfers_task,
    recover_stuck_transfers_task,
)


@shared_task(name="transfers.recover_stuck_transfers_beat")
def recover_stuck_transfers_beat():
    """Celery Beat periodic timer: triggers recover_stuck_transfers_task worker task."""
    logger.info("recover_stuck_transfers_beat: triggering recover_stuck_transfers_task worker task")
    # pyrefly: ignore [not-callable]
    recover_stuck_transfers_task.delay()


@shared_task(name="transfers.reconcile_processing_transfers_beat")
def reconcile_processing_transfers_beat():
    """Celery Beat periodic timer: triggers reconcile_processing_transfers_task worker task."""
    logger.info("reconcile_processing_transfers_beat: triggering reconcile_processing_transfers_task worker task")
    # pyrefly: ignore [not-callable]
    reconcile_processing_transfers_task.delay()
