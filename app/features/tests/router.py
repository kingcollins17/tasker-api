from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, status

from app.core.error_handler import AppErrorHandler
from app.core.services.cache import get_cache_service
from app.core.services.sse_manager import SSEManager
from app.features.credibility.celery.tasks import sync_user_credibility_score
from app.features.reviews.celery.tasks import sync_user_ratings
from app.features.tasks.celery.metrics import sync_provider_metrics
from app.features.vetting.celery.tasks import sync_provider_tier

router = APIRouter()


@router.post("/test-in-app/{user_id}")
async def test_in_app_notification(
    user_id: str,
    task_id: Optional[str] = None,
    type: Optional[str] = "test",
    title: Optional[str] = "Test Notification",
    body: Optional[str] = "This is a test in-app notification",
    data: Optional[dict] = None,
    priority: Optional[str] = "normal",
):
    """Test endpoint for sending an in-app notification via SSEManager.publish."""
    try:
        cache = get_cache_service()

        data = data or {}
        if task_id:
            data["task_id"] = task_id
        if type:
            data["type"] = type

        notification_payload = {
            "notification_id": "test-1234",
            "type": type,
            "title": title,
            "body": body,
            "data": data,
            "priority": priority,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        receivers = await SSEManager.publish(cache, user_id, notification_payload)
        return {"status": "sent", "receivers": receivers, "user_id": user_id}
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send test notification",
        )


@router.post("/sync-user-stats/{user_id}")
async def test_sync_user_stats(user_id: str):
    """Test endpoint for triggering user rating, credibility, provider tier, and provider metrics sync Celery tasks."""
    try:
        task_ids = {
            # pyrefly: ignore [not-callable]
            "sync_user_ratings": sync_user_ratings.delay(user_id).id,
            # pyrefly: ignore [not-callable]
            "sync_user_credibility_score": sync_user_credibility_score.delay(user_id).id,
            # pyrefly: ignore [not-callable]
            "sync_provider_tier": sync_provider_tier.delay(user_id).id,
            # pyrefly: ignore [not-callable]
            "sync_provider_metrics": sync_provider_metrics.delay(user_id).id,
        }
        return {
            "status": "triggered",
            "user_id": user_id,
            "tasks": task_ids,
        }
    except HTTPException:
        raise
    except Exception as e:
        AppErrorHandler.handleError(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to trigger user stats sync tasks",
        )
