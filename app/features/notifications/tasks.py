from app.core.utils.timer import Timer
from app.core.services.logger_service import get_logger_service_manual
"""Scalable notification pipeline tasks.

Pipeline:
    process_notification  (fan-out)  →  process_recipient_batch  (batch worker)
        →  send_email_batch / send_sms_batch / send_push_batch / send_whatsapp_batch

Design principles:
    - The fan-out task NEVER loads all recipients into memory; it paginates in batches.
    - Deliveries are bulk-inserted (one INSERT for thousands of rows).
    - Channel tasks run on dedicated queues so a slow provider can't block others.
    - Each channel task processes a batch of deliveries, not a single one.
"""

import asyncio
import json
from collections import defaultdict
from typing import Dict, List, Optional

from celery import shared_task
from sqlmodel import col, select

from app.core.database import async_session_maker
from app.core.logging import logger
from app.core.models.notifications import (
    DeliveryStatus,
    Notification,
    NotificationChannel,
    NotificationDelivery,
    NotificationPreference,
    NotificationRecipient,
    RecipientStatus,
)
from app.core.models.users import User
from app.core.repository import QueryOptions, Repository
from app.core.services import email_service, sms_service, whatsapp_service
from app.core.services.cache import get_cache_service
from app.core.services.cloud_messaging import MockCloudMessagingService
from app.core.services.notification_pubsub import NOTIFICATION_CHANNEL
from app.core.utils.datetime_helper import utc_now
from app.core.utils.celery import run_async


# ── Helpers ──────────────────────────────────────────────────────────────────

BATCH_SIZE = 1000


# ── Step 1: Fan-out task ─────────────────────────────────────────────────────


@shared_task(
    name="notifications.process_notification",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def process_notification(self, notification_id: str) -> None:
    """Fan-out: paginate recipients in batches and dispatch batch workers.

    This task NEVER sends an email. It only fans out work.
    For 2M recipients → 2,000 batch tasks (1,000 recipients each).
    """
    logger.info(f"[Pipeline] Fan-out started for notification {notification_id}")

    try:
        run_async(_fan_out_notification(notification_id))
        logger.info(f"[Pipeline] Fan-out complete for notification {notification_id}")
    except Exception as exc:
        logger.error(
            f"[Pipeline] Fan-out failed for notification {notification_id}: {exc}"
        )
        raise self.retry(exc=exc)


async def _fan_out_notification(notification_id: str) -> None:
    """Paginate through notification_recipients and dispatch batch tasks."""
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            notification_repo = Repository(Notification, session)
            recipient_repo = Repository(NotificationRecipient, session)

            # Validate notification exists and hasn't expired
            notification = await notification_repo.get(notification_id)
            if not notification:
                logger.warning(
                    f"[Pipeline] Notification {notification_id} not found, aborting fan-out."
                )
                return

            if notification.expires_at and utc_now() > notification.expires_at:
                logger.info(
                    f"[Pipeline] Notification {notification_id} expired, skipping delivery."
                )
                return

            # Paginate through recipients — never load all into memory
            offset = 0
            total_batches = 0

            while True:
                stmt = (
                    select(NotificationRecipient)
                    .where(col(NotificationRecipient.notification_id) == notification_id)
                    .offset(offset)
                    .limit(BATCH_SIZE)
                )
                result = await recipient_repo.execute(stmt)
                recipients = list(result.all())

                if not recipients:
                    break

                recipient_ids = [r.id for r in recipients]
                # pyrefly: ignore [not-callable]
                process_recipient_batch.delay(
                    notification_id, recipient_ids
                )  # pyright: ignore [reportCallIssue]

                total_batches += 1
                offset += BATCH_SIZE

            logger.info(
                f"[Pipeline] Dispatched {total_batches} batch task(s) for notification {notification_id}"
            )


            await system_logger.metric('process_notification', timer.stop(), source='celery.process_notification')
        except Exception as e:
            await system_logger.error(f'process_notification Failed: {str(e)}', source='celery.process_notification')
            raise e
# ── Step 2: Batch worker ────────────────────────────────────────────────────


@shared_task(
    name="notifications.process_recipient_batch",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def process_recipient_batch(
    self, notification_id: str, recipient_ids: List[str]
) -> None:
    """Check preferences, bulk-insert deliveries, and dispatch channel batch tasks.

    Receives up to 1,000 recipient IDs per invocation.
    """
    logger.info(
        f"[Pipeline] Processing batch of {len(recipient_ids)} recipients "
        f"for notification {notification_id}"
    )

    try:
        run_async(_process_batch(notification_id, recipient_ids))
    except Exception as exc:
        logger.error(
            f"[Pipeline] Batch processing failed for notification {notification_id}: {exc}"
        )
        raise self.retry(exc=exc)


async def _process_batch(notification_id: str, recipient_ids: List[str]) -> None:
    """Build deliveries from preferences, bulk-insert, and fan out to channel workers."""
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            notification_repo = Repository(Notification, session)
            recipient_repo = Repository(NotificationRecipient, session)
            delivery_repo = Repository(NotificationDelivery, session)
            preference_repo = Repository(NotificationPreference, session)

            notification = await notification_repo.get(notification_id)
            if not notification:
                logger.warning(
                    f"[Pipeline] Notification {notification_id} not found in batch worker."
                )
                return

            # Load all recipients in this batch
            recipients = []
            for rid in recipient_ids:
                r = await recipient_repo.get(rid)
                if r:
                    recipients.append(r)

            if not recipients:
                return

            # Build a map of user_id → [recipient_ids] for preference lookup
            user_to_recipients: Dict[str, List[str]] = defaultdict(list)
            recipient_to_user: Dict[str, str] = {}
            for r in recipients:
                user_to_recipients[r.recipient_id].append(r.id)
                recipient_to_user[r.id] = r.recipient_id

            unique_user_ids = list(user_to_recipients.keys())

            # Load preferences for all users in this batch in one query
            pref_stmt = (
                select(NotificationPreference)
                .where(col(NotificationPreference.user_id).in_(unique_user_ids))
                .where(col(NotificationPreference.notification_type) == notification.type)
            )
            pref_result = await preference_repo.execute(pref_stmt)
            all_prefs = list(pref_result.all())

            # Build user_id → set of enabled channels
            user_channels: Dict[str, List[NotificationChannel]] = {}
            user_has_prefs: set = set()
            for pref in all_prefs:
                user_has_prefs.add(pref.user_id)
                if pref.enabled:
                    user_channels.setdefault(pref.user_id, []).append(pref.channel)

            # Users with no preferences → default to all channels
            default_channels = list(NotificationChannel)
            for uid in unique_user_ids:
                if uid not in user_has_prefs:
                    user_channels[uid] = default_channels

            # Build delivery objects
            deliveries: List[NotificationDelivery] = []
            for recipient in recipients:
                enabled = user_channels.get(
                    recipient.recipient_id, [NotificationChannel.IN_APP]
                )

                for channel in enabled:
                    if (
                        notification.channels is not None
                        and channel.value not in notification.channels
                    ):
                        continue
                    deliveries.append(
                        NotificationDelivery(
                            recipient_id=recipient.id,
                            channel=channel,
                            status=DeliveryStatus.PENDING,
                            attempt=1,
                        )
                    )

            # Bulk-insert all deliveries in one SQL statement
            if deliveries:
                await delivery_repo.bulk_add(deliveries)

            # Mark recipients as SENT in bulk to activate/show them in the user's in-app feed.
            # This is done now because the notification has been successfully fanned out and
            # delivery tasks have been dispatched. Individual channel delivery success/failure
            # is tracked asynchronously via NotificationDelivery.
            await recipient_repo.bulk_update(
                recipient_ids, {"status": RecipientStatus.SENT}
            )

            # Group delivery IDs by channel and dispatch channel batch tasks
            channel_delivery_ids: Dict[str, List[str]] = defaultdict(list)
            for d in deliveries:
                channel_delivery_ids[d.channel.value].append(d.id)

            for channel, delivery_ids in channel_delivery_ids.items():
                if channel == NotificationChannel.EMAIL.value:
                    # pyrefly: ignore [not-callable]
                    send_email_batch.delay(notification_id, delivery_ids)
                elif channel == NotificationChannel.SMS.value:
                    # pyrefly: ignore [not-callable]
                    send_sms_batch.delay(notification_id, delivery_ids)
                elif channel == NotificationChannel.PUSH.value:
                    # pyrefly: ignore [not-callable]
                    send_push_batch.delay(notification_id, delivery_ids)
                elif channel == NotificationChannel.WHATSAPP.value:
                    # pyrefly: ignore [not-callable]
                    send_whatsapp_batch.delay(notification_id, delivery_ids)
                elif channel == NotificationChannel.IN_APP.value:
                    # Mark in-app deliveries as delivered
                    await delivery_repo.bulk_update(
                        delivery_ids, {"status": DeliveryStatus.DELIVERED.value}
                    )
                    # Publish real-time WebSocket events via Redis Pub/Sub
                    cache = get_cache_service()
                    notification_payload = {
                        "notification_id": notification.id,
                        "type": notification.type.value,
                        "title": notification.title,
                        "body": notification.body,
                        "data": notification.data,
                        "priority": notification.priority.value,
                        "created_at": notification.created_at.isoformat(),
                    }
                    for recipient in recipients:
                        message = json.dumps(
                            {
                                "user_id": recipient.recipient_id,
                                "notification": notification_payload,
                            }
                        )
                        await cache.publish(NOTIFICATION_CHANNEL, message)

            logger.info(
                f"[Pipeline] Batch complete: {len(deliveries)} deliveries created, "
                f"channels dispatched: {list(channel_delivery_ids.keys())}"
            )


            await system_logger.metric('process_recipient_batch', timer.stop(), source='celery.process_recipient_batch')
        except Exception as e:
            await system_logger.error(f'process_recipient_batch Failed: {str(e)}', source='celery.process_recipient_batch')
            raise e
# ── Step 3: Channel workers ─────────────────────────────────────────────────


@shared_task(
    name="notifications.send_email_batch",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def send_email_batch(self, notification_id: str, delivery_ids: List[str]) -> None:
    """Send emails for a batch of deliveries."""
    logger.info(
        f"[Email] Processing {len(delivery_ids)} deliveries for notification {notification_id}"
    )
    run_async(_send_email_batch(notification_id, delivery_ids))


async def _send_email_batch(notification_id: str, delivery_ids: List[str]) -> None:
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            notification_repo = Repository(Notification, session)
            delivery_repo = Repository(NotificationDelivery, session)
            recipient_repo = Repository(NotificationRecipient, session)
            user_repo = Repository(User, session)

            notification = await notification_repo.get(notification_id)
            if not notification:
                return

            succeeded_ids: List[str] = []
            failed_ids: List[str] = []
            now = utc_now()

            for delivery_id in delivery_ids:
                delivery = await delivery_repo.get(delivery_id)
                if not delivery:
                    continue

                recipient = await recipient_repo.get(delivery.recipient_id)
                if not recipient:
                    failed_ids.append(delivery_id)
                    continue

                user = await user_repo.get(recipient.recipient_id)
                if not user:
                    failed_ids.append(delivery_id)
                    continue

                try:
                    result = await email_service.send_email(
                        to_emails=user.email,
                        subject=notification.title,
                        body=notification.body,
                    )
                    if result.get(user.email, False):
                        succeeded_ids.append(delivery_id)
                    else:
                        failed_ids.append(delivery_id)
                except Exception as exc:
                    logger.error(f"[Email] Failed to send delivery {delivery_id}: {exc}")
                    failed_ids.append(delivery_id)

            # Bulk-update statuses
            if succeeded_ids:
                await delivery_repo.bulk_update(
                    succeeded_ids,
                    {
                        "status": DeliveryStatus.DELIVERED,
                        "sent_at": now,
                        "delivered_at": now,
                    },
                )
            if failed_ids:
                await delivery_repo.bulk_update(
                    failed_ids,
                    {
                        "status": DeliveryStatus.FAILED,
                        "failure_reason": "Provider returned failure or user not found",
                        "sent_at": now,
                    },
                )

            await system_logger.metric('send_email_batch', timer.stop(), source='celery.send_email_batch')
        except Exception as e:
            await system_logger.error(f'send_email_batch Failed: {str(e)}', source='celery.send_email_batch')
            raise e
    logger.info(
        f"[Email] Batch complete: {len(succeeded_ids)} sent, {len(failed_ids)} failed"
    )


@shared_task(
    name="notifications.send_sms_batch",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def send_sms_batch(self, notification_id: str, delivery_ids: List[str]) -> None:
    """Send SMS messages for a batch of deliveries."""
    logger.info(
        f"[SMS] Processing {len(delivery_ids)} deliveries for notification {notification_id}"
    )
    run_async(_send_sms_batch(notification_id, delivery_ids))


async def _send_sms_batch(notification_id: str, delivery_ids: List[str]) -> None:
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            notification_repo = Repository(Notification, session)
            delivery_repo = Repository(NotificationDelivery, session)
            recipient_repo = Repository(NotificationRecipient, session)
            user_repo = Repository(User, session)

            notification = await notification_repo.get(notification_id)
            if not notification:
                return

            succeeded_ids: List[str] = []
            failed_ids: List[str] = []
            perm_failed_ids: List[str] = []
            now = utc_now()

            for delivery_id in delivery_ids:
                delivery = await delivery_repo.get(delivery_id)
                if not delivery:
                    continue

                recipient = await recipient_repo.get(delivery.recipient_id)
                if not recipient:
                    failed_ids.append(delivery_id)
                    continue

                user = await user_repo.get(recipient.recipient_id)
                if not user:
                    failed_ids.append(delivery_id)
                    continue

                if not user.phone_number:
                    perm_failed_ids.append(delivery_id)
                    continue

                try:
                    result = await sms_service.send_sms(
                        phone_numbers=user.phone_number,
                        message=f"{notification.title}: {notification.body}",
                    )
                    if result.get(user.phone_number, False):
                        succeeded_ids.append(delivery_id)
                    else:
                        failed_ids.append(delivery_id)
                except Exception as exc:
                    logger.error(f"[SMS] Failed to send delivery {delivery_id}: {exc}")
                    failed_ids.append(delivery_id)

            if succeeded_ids:
                await delivery_repo.bulk_update(
                    succeeded_ids,
                    {
                        "status": DeliveryStatus.DELIVERED,
                        "sent_at": now,
                        "delivered_at": now,
                    },
                )
            if failed_ids:
                await delivery_repo.bulk_update(
                    failed_ids,
                    {
                        "status": DeliveryStatus.FAILED,
                        "failure_reason": "Provider returned failure or user not found",
                        "sent_at": now,
                    },
                )
            if perm_failed_ids:
                await delivery_repo.bulk_update(
                    perm_failed_ids,
                    {
                        "status": DeliveryStatus.PERMANENT_FAILURE,
                        "failure_reason": "User has no phone number",
                    },
                )

            await system_logger.metric('send_sms_batch', timer.stop(), source='celery.send_sms_batch')
        except Exception as e:
            await system_logger.error(f'send_sms_batch Failed: {str(e)}', source='celery.send_sms_batch')
            raise e
    logger.info(
        f"[SMS] Batch complete: {len(succeeded_ids)} sent, {len(failed_ids)} failed"
    )


@shared_task(
    name="notifications.send_push_batch",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def send_push_batch(self, notification_id: str, delivery_ids: List[str]) -> None:
    """Send push notifications for a batch of deliveries."""
    logger.info(
        f"[Push] Processing {len(delivery_ids)} deliveries for notification {notification_id}"
    )
    run_async(_send_push_batch(notification_id, delivery_ids))


async def _send_push_batch(notification_id: str, delivery_ids: List[str]) -> None:
    push_svc = MockCloudMessagingService()

    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            notification_repo = Repository(Notification, session)
            delivery_repo = Repository(NotificationDelivery, session)
            recipient_repo = Repository(NotificationRecipient, session)
            user_repo = Repository(User, session)

            notification = await notification_repo.get(notification_id)
            if not notification:
                return

            data_payload = (
                {k: str(v) for k, v in notification.data.items()}
                if notification.data
                else None
            )

            succeeded_ids: List[str] = []
            failed_ids: List[str] = []
            perm_failed_ids: List[str] = []
            now = utc_now()

            for delivery_id in delivery_ids:
                delivery = await delivery_repo.get(delivery_id)
                if not delivery:
                    continue

                recipient = await recipient_repo.get(delivery.recipient_id)
                if not recipient:
                    failed_ids.append(delivery_id)
                    continue

                user = await user_repo.get(recipient.recipient_id)
                if not user:
                    failed_ids.append(delivery_id)
                    continue

                active_devices = [
                    d for d in user.devices if d.is_active and d.messaging_token
                ]
                if not active_devices:
                    perm_failed_ids.append(delivery_id)
                    continue

                try:
                    send_results = []
                    for device in active_devices:
                        res = await push_svc.send_message(
                            token=device.messaging_token,
                            title=notification.title,
                            body=notification.body,
                            data=data_payload,
                        )
                        send_results.append(res)

                    if any(send_results):
                        succeeded_ids.append(delivery_id)
                    else:
                        failed_ids.append(delivery_id)
                except Exception as exc:
                    logger.error(f"[Push] Failed to send delivery {delivery_id}: {exc}")
                    failed_ids.append(delivery_id)

            if succeeded_ids:
                await delivery_repo.bulk_update(
                    succeeded_ids,
                    {
                        "status": DeliveryStatus.DELIVERED,
                        "sent_at": now,
                        "delivered_at": now,
                    },
                )
            if failed_ids:
                await delivery_repo.bulk_update(
                    failed_ids,
                    {
                        "status": DeliveryStatus.FAILED,
                        "failure_reason": "Provider returned failure or user not found",
                        "sent_at": now,
                    },
                )
            if perm_failed_ids:
                await delivery_repo.bulk_update(
                    perm_failed_ids,
                    {
                        "status": DeliveryStatus.PERMANENT_FAILURE,
                        "failure_reason": "User has no active push devices",
                    },
                )

            await system_logger.metric('send_push_batch', timer.stop(), source='celery.send_push_batch')
        except Exception as e:
            await system_logger.error(f'send_push_batch Failed: {str(e)}', source='celery.send_push_batch')
            raise e
    logger.info(
        f"[Push] Batch complete: {len(succeeded_ids)} sent, {len(failed_ids)} failed"
    )


@shared_task(
    name="notifications.send_whatsapp_batch",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def send_whatsapp_batch(self, notification_id: str, delivery_ids: List[str]) -> None:
    """Send WhatsApp messages for a batch of deliveries."""
    logger.info(
        f"[WhatsApp] Processing {len(delivery_ids)} deliveries for notification {notification_id}"
    )
    run_async(_send_whatsapp_batch(notification_id, delivery_ids))


async def _send_whatsapp_batch(notification_id: str, delivery_ids: List[str]) -> None:
    async with async_session_maker() as session:
        system_logger = get_logger_service_manual(session)
        timer = Timer()
        timer.start()
        try:
            notification_repo = Repository(Notification, session)
            delivery_repo = Repository(NotificationDelivery, session)
            recipient_repo = Repository(NotificationRecipient, session)
            user_repo = Repository(User, session)

            notification = await notification_repo.get(notification_id)
            if not notification:
                return

            succeeded_ids: List[str] = []
            failed_ids: List[str] = []
            perm_failed_ids: List[str] = []
            now = utc_now()

            for delivery_id in delivery_ids:
                delivery = await delivery_repo.get(delivery_id)
                if not delivery:
                    continue

                recipient = await recipient_repo.get(delivery.recipient_id)
                if not recipient:
                    failed_ids.append(delivery_id)
                    continue

                user = await user_repo.get(recipient.recipient_id)
                if not user:
                    failed_ids.append(delivery_id)
                    continue

                if not user.phone_number:
                    perm_failed_ids.append(delivery_id)
                    continue

                try:
                    result = await whatsapp_service.send_message(
                        phone_numbers=user.phone_number,
                        message=f"{notification.title}: {notification.body}",
                    )
                    if result.get(user.phone_number, False):
                        succeeded_ids.append(delivery_id)
                    else:
                        failed_ids.append(delivery_id)
                except Exception as exc:
                    logger.error(f"[WhatsApp] Failed to send delivery {delivery_id}: {exc}")
                    failed_ids.append(delivery_id)

            if succeeded_ids:
                await delivery_repo.bulk_update(
                    succeeded_ids,
                    {
                        "status": DeliveryStatus.DELIVERED,
                        "sent_at": now,
                        "delivered_at": now,
                    },
                )
            if failed_ids:
                await delivery_repo.bulk_update(
                    failed_ids,
                    {
                        "status": DeliveryStatus.FAILED,
                        "failure_reason": "Provider returned failure or user not found",
                        "sent_at": now,
                    },
                )
            if perm_failed_ids:
                await delivery_repo.bulk_update(
                    perm_failed_ids,
                    {
                        "status": DeliveryStatus.PERMANENT_FAILURE,
                        "failure_reason": "User has no phone number for WhatsApp",
                    },
                )

            await system_logger.metric('send_whatsapp_batch', timer.stop(), source='celery.send_whatsapp_batch')
        except Exception as e:
            await system_logger.error(f'send_whatsapp_batch Failed: {str(e)}', source='celery.send_whatsapp_batch')
            raise e
    logger.info(
        f"[WhatsApp] Batch complete: {len(succeeded_ids)} sent, {len(failed_ids)} failed"
    )
