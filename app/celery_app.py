from celery import Celery
from kombu import Queue

from app.core.config import settings

celery_app = Celery(
    "tasker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.features.tasks.celery.dispatch",
        "app.features.tasks.celery.metrics",
        "app.features.reviews.celery.tasks",
        "app.features.credibility.celery.tasks",
        "app.features.payments.celery.tasks",
    ]
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
        Queue("tasks"),
        Queue("payments"),
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
        "tasks.match_providers_for_task": {"queue": "tasks"},
        "tasks.notify_providers_of_task": {"queue": "tasks"},
        "tasks.sync_provider_metrics": {"queue": "tasks"},
        "tasks.sync_service_duration_metrics": {"queue": "tasks"},
        "tasks.handle_dispatch_ping_timeout": {"queue": "tasks"},
        "tasks.start_dispatch_workflow": {"queue": "tasks"},
        "reviews.sync_user_ratings": {"queue": "tasks"},
        "credibility.sync_user_credibility_score": {"queue": "tasks"},
        "payments.process_task_payment": {"queue": "payments"},
        "payments.process_provider_payout": {"queue": "payments"},
        "payments.process_debt_settlement": {"queue": "payments"},
    },
)

# Auto-discover tasks from our application packages
celery_app.autodiscover_tasks([
    "app.core",
    "app.features.users",
    "app.features.notifications",
    "app.features.tasks.celery",
    "app.features.credibility.celery",
    "app.features.payments.celery",
])
