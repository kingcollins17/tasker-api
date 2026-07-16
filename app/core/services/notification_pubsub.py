"""Redis Pub/Sub bridge for real-time in-app notifications.

Subscriber side (async, runs in the FastAPI event loop):
    ``start_notification_listener()`` subscribes to the Redis Pub/Sub channel
    via the existing ``CacheService`` and dispatches incoming messages to the
    local ``ConnectionManager``.

Publisher side (async, called from Celery async helpers):
    Uses ``CacheService.publish()`` directly in the async task context.

Because every server instance runs its own subscriber, a notification
published by *any* Celery worker reaches *every* instance. Each instance
checks whether it holds the target user's WebSocket and only delivers
locally — no duplicate messages are sent.
"""

import asyncio
import json
from typing import Optional

from redis.asyncio.client import PubSub

from app.core.logging import logger
from app.core.services.cache import get_cache_service
from app.core.services.connection_manager import get_connection_manager

NOTIFICATION_CHANNEL = "in_app_notifications"

# Background asyncio task handle
_listener_task: Optional[asyncio.Task] = None
_pubsub: Optional[PubSub] = None


# ── Subscriber (async — runs inside FastAPI event loop) ──────────────────────


async def _listen() -> None:
    """Internal coroutine that subscribes and dispatches messages."""
    global _pubsub

    cache = get_cache_service()
    manager = get_connection_manager()

    while True:
        try:
            _pubsub = cache.pubsub()
            await _pubsub.subscribe(NOTIFICATION_CHANNEL)
            logger.info(f"[PubSub] Subscribed to '{NOTIFICATION_CHANNEL}' channel.")

            while True:
                # Use get_message with a timeout so we can periodically ping
                # the connection to keep it alive on Serverless Redis (Upstash)
                message = await _pubsub.get_message(
                    ignore_subscribe_messages=False, timeout=60.0
                )
                if message is None:
                    await _pubsub.ping()
                    continue

                logger.warning(f"[PubSub] Message received {message}")

                if message["type"] != "message":
                    continue

                try:
                    logger.info(f"[PubSub] Received raw message: {message['data']}")
                    data = json.loads(message["data"] or "{}")
                    user_id = data.get("user_id")
                    notification = data.get("notification")

                    if not user_id or not notification:
                        logger.warning(
                            "[PubSub] Message missing 'user_id' or 'notification' payload."
                        )
                        continue

                    logger.info(
                        f"[PubSub] Attempting to deliver notification to user_id: {user_id}"
                    )
                    success = await manager.send_to_user(user_id, notification)
                    if success:
                        logger.info(
                            f"[PubSub] Successfully delivered notification to user_id: {user_id}"
                        )
                    else:
                        logger.info(
                            f"[PubSub] User {user_id} not connected to this instance, message not delivered."
                        )
                except json.JSONDecodeError:
                    logger.warning("[PubSub] Received non-JSON message, skipping.")
                except Exception as e:
                    logger.error(f"[PubSub] Error processing message: {e}")

        except asyncio.CancelledError:
            logger.info("[PubSub] Listener task cancelled.")
            break
        except Exception as e:
            logger.error(
                f"[PubSub] Connection error in listener loop: {e}. Reconnecting in 5s..."
            )
            await asyncio.sleep(5)
        finally:
            if _pubsub:
                try:
                    await _pubsub.unsubscribe(NOTIFICATION_CHANNEL)
                    await _pubsub.close()
                except Exception:
                    pass
            logger.info("[PubSub] Cleaned up subscriber connection.")


async def start_notification_listener() -> None:
    """Start the Redis Pub/Sub listener as a background asyncio task."""
    global _listener_task
    if _listener_task is not None and not _listener_task.done():
        logger.warning("[PubSub] Listener already running.")
        return

    _listener_task = asyncio.create_task(_listen())
    logger.info("[PubSub] Notification listener started.")


async def stop_notification_listener() -> None:
    """Stop the background listener task gracefully."""
    global _listener_task
    if _listener_task is None or _listener_task.done():
        return

    _listener_task.cancel()
    try:
        await _listener_task
    except asyncio.CancelledError:
        pass
    _listener_task = None
    logger.info("[PubSub] Notification listener stopped.")
