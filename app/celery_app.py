from celery import Celery
from kombu import Queue

from app.core.config import settings

celery_app = Celery(
    "tasker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,

    # ── Dedicated queues ──────────────────────────────────────────────────
    task_queues=(
        Queue("default"),
        Queue("notifications"),
        Queue("email"),
        Queue("sms"),
        Queue("push"),
        Queue("whatsapp"),
    ),
    task_default_queue="default",

    # ── Route tasks to their queues ───────────────────────────────────────
    task_routes={
        "notifications.process_notification": {"queue": "notifications"},
        "notifications.process_recipient_batch": {"queue": "notifications"},
        "notifications.send_email_batch": {"queue": "email"},
        "notifications.send_sms_batch": {"queue": "sms"},
        "notifications.send_push_batch": {"queue": "push"},
        "notifications.send_whatsapp_batch": {"queue": "whatsapp"},
    },
)

# Auto-discover tasks from our application packages
celery_app.autodiscover_tasks([
    "app.core",
    "app.features.users",
    "app.features.notifications",
])
