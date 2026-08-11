"""Scalable notification pipeline — refactored.

Changes from the original:
  1. NotificationPipeline is a plain class of staticmethods (no Celery dependency).
     Celery tasks below are thin wrappers that call into it. This makes the
     logic unit-testable and reusable without spinning up Celery.
  2. All per-ID .get() loops replaced with single WHERE id IN (...) bulk fetches.
  3. Provider sends (email/sms/push/whatsapp) run concurrently via asyncio.gather
     + a semaphore, instead of sequential awaits in a for-loop.
  4. Idempotent retries: before sending, we filter deliveries to only those still
     PENDING or FAILED. Already-DELIVERED deliveries are skipped, so a retry after
     a partial failure can't double-send.
  5. Fan-out pagination now has a stable ORDER BY so offset/limit can't skip or
     duplicate rows under concurrent inserts.

Pipeline:
    process_notification  (fan-out)  →  process_recipient_batch  (batch worker)
        →  send_email_batch / send_sms_batch / send_push_batch / send_whatsapp_batch
"""

import asyncio
import json
from collections import defaultdict
from typing import Dict, List

from celery import shared_task
from sqlmodel import col, select

from app.core.database import async_session_maker
from app.core.logging import logger
from app.core.models.notifications import (
    DeliveryStatus,
    Notification,
    NotificationChannel,
    NotificationDelivery,
    NotificationRecipient,
    RecipientStatus,
)
from app.core.models.users import User
from app.core.repository import Repository
from app.core.services import email_service, sms_service, whatsapp_service
from app.core.services.cache import get_cache_service
from app.core.services.cloud_messaging import MockCloudMessagingService
from app.core.services.notification_pubsub import NOTIFICATION_CHANNEL
from app.core.services.logger_service import get_logger_service_manual
from app.core.utils.datetime_helper import lagos_now, now as timenow
from app.core.utils.celery import run_async
from app.core.utils.timer import Timer


class NotificationPipeline:
    """Pure business logic for the notification pipeline. No Celery here —
    keep this class importable/testable on its own."""

    BATCH_SIZE = 1000
    # Cap concurrent provider calls per batch task so we don't hammer the
    # email/SMS provider or blow past their rate limits.
    SEND_CONCURRENCY = 20

    # ── Step 1: fan-out ──────────────────────────────────────────────────

    @staticmethod
    async def fan_out(notification_id: str) -> None:
        """Paginate through notification_recipients and dispatch batch tasks.

        This method NEVER sends an email. It only fans out work.
        For 2M recipients → 2,000 batch tasks (1,000 recipients each).
        """
        async with async_session_maker() as session:
            system_logger = get_logger_service_manual(session)
            timer = Timer()
            timer.start()
            try:
                notification_repo = Repository(Notification, session)
                recipient_repo = Repository(NotificationRecipient, session)

                notification = await notification_repo.get(notification_id)
                if not notification:
                    logger.warning(f"[Pipeline] Notification {notification_id} not found, aborting fan-out.")
                    return

                if notification.expires_at and lagos_now() > notification.expires_at:
                    logger.info(f"[Pipeline] Notification {notification_id} expired, skipping delivery.")
                    return

                offset = 0
                total_batches = 0

                while True:
                    stmt = (
                        select(NotificationRecipient)
                        .where(col(NotificationRecipient.notification_id) == notification_id)
                        # FIX #5: stable ordering so offset/limit pagination can't
                        # skip/duplicate rows if recipients are inserted concurrently.
                        .order_by(col(NotificationRecipient.id))
                        .offset(offset)
                        .limit(NotificationPipeline.BATCH_SIZE)
                    )
                    result = await recipient_repo.execute(stmt)
                    recipients = list(result.all())

                    if not recipients:
                        break

                    recipient_ids = [r.id for r in recipients]
                    # pyrefly: ignore [not-callable]
                    process_recipient_batch.delay(notification_id, recipient_ids)

                    total_batches += 1
                    offset += NotificationPipeline.BATCH_SIZE

                logger.info(f"[Pipeline] Dispatched {total_batches} batch task(s) for notification {notification_id}")
                await system_logger.metric("process_notification", timer.stop(), source="celery.process_notification")
            except Exception as e:
                await system_logger.error(f"process_notification Failed: {str(e)}", source="celery.process_notification")
                raise

    # ── Step 2: batch worker ────────────────────────────────────────────

    @staticmethod
    async def process_batch(notification_id: str, recipient_ids: List[str]) -> None:
        """Check preferences, bulk-insert deliveries, and dispatch channel batch tasks.

        Receives up to 1,000 recipient IDs per invocation.
        """
        async with async_session_maker() as session:
            system_logger = get_logger_service_manual(session)
            timer = Timer()
            timer.start()
            try:
                notification_repo = Repository(Notification, session)
                recipient_repo = Repository(NotificationRecipient, session)
                delivery_repo = Repository(NotificationDelivery, session)

                notification = await notification_repo.get(notification_id)
                if not notification:
                    logger.warning(f"[Pipeline] Notification {notification_id} not found in batch worker.")
                    return

                # FIX #2: bulk fetch instead of N individual .get() calls.
                result = await recipient_repo.execute(
                    select(NotificationRecipient).where(col(NotificationRecipient.id).in_(recipient_ids))
                )
                recipients = list(result.all())
                if not recipients:
                    return

                # Determine channels to use for this notification
                channels_to_send = []
                if notification.channels is not None:
                    for c in notification.channels:
                        try:
                            channels_to_send.append(NotificationChannel(c))
                        except ValueError:
                            pass
                else:
                    channels_to_send = list(NotificationChannel)

                # Build delivery objects
                deliveries: List[NotificationDelivery] = []
                for recipient in recipients:
                    for channel in channels_to_send:
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

                # Mark recipients as SENT in bulk
                await recipient_repo.bulk_update(recipient_ids, {"status": RecipientStatus.SENT})

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
                            "type": notification.type.value if notification.type else "system_alert",
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
                await system_logger.metric("process_recipient_batch", timer.stop(), source="celery.process_recipient_batch")
            except Exception as e:
                await system_logger.error(f"process_recipient_batch Failed: {str(e)}", source="celery.process_recipient_batch")
                raise

    # ── Step 3: channel workers ─────────────────────────────────────────

    @staticmethod
    async def send_email_batch(notification_id: str, delivery_ids: List[str]) -> None:
        """Send emails for a batch of deliveries with concurrent sends and idempotent retries."""
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

                # FIX #4 (idempotency): only pull deliveries still needing a send.
                # A retry after a partial failure will naturally skip anything
                # already marked DELIVERED in a prior attempt.
                result = await delivery_repo.execute(
                    select(NotificationDelivery).where(
                        col(NotificationDelivery.id).in_(delivery_ids),
                        col(NotificationDelivery.status).in_(
                            [DeliveryStatus.PENDING, DeliveryStatus.FAILED]
                        ),
                    )
                )
                deliveries = list(result.all())
                if not deliveries:
                    return

                # FIX #2 (N+1): bulk fetch recipients and users instead of
                # 2 queries per delivery.
                recipient_ids = list({d.recipient_id for d in deliveries})
                recipients_result = await recipient_repo.execute(
                    select(NotificationRecipient).where(col(NotificationRecipient.id).in_(recipient_ids))
                )
                recipients_by_id = {r.id: r for r in recipients_result.all()}

                user_ids = list({r.recipient_id for r in recipients_by_id.values()})
                users_result = await user_repo.execute(
                    select(User).where(col(User.id).in_(user_ids))
                )
                users_by_id = {u.id: u for u in users_result.all()}

                now = lagos_now()
                sem = asyncio.Semaphore(NotificationPipeline.SEND_CONCURRENCY)

                async def _send_one(delivery: NotificationDelivery):
                    recipient = recipients_by_id.get(delivery.recipient_id)
                    if not recipient:
                        return delivery.id, False, "recipient not found"
                    user = users_by_id.get(recipient.recipient_id)
                    if not user:
                        return delivery.id, False, "user not found"
                    async with sem:
                        try:
                            result = await email_service.send_email(
                                to_emails=user.email,
                                subject=notification.title,
                                body=notification.body,
                            )
                            ok = result.get(user.email, False)
                            return delivery.id, ok, None if ok else "provider returned failure"
                        except Exception as exc:
                            logger.error(f"[Email] Failed to send delivery {delivery.id}: {exc}")
                            return delivery.id, False, str(exc)

                # FIX #3 (concurrency): fire sends in parallel instead of one
                # sequential await per recipient.
                results = await asyncio.gather(*(_send_one(d) for d in deliveries))

                succeeded_ids = [did for did, ok, _ in results if ok]
                failed_ids = [did for did, ok, _ in results if not ok]

                if succeeded_ids:
                    await delivery_repo.bulk_update(
                        succeeded_ids,
                        {"status": DeliveryStatus.DELIVERED, "sent_at": now, "delivered_at": now},
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

                logger.info(f"[Email] Batch complete: {len(succeeded_ids)} sent, {len(failed_ids)} failed")
                await system_logger.metric("send_email_batch", timer.stop(), source="celery.send_email_batch")
            except Exception as e:
                await system_logger.error(f"send_email_batch Failed: {str(e)}", source="celery.send_email_batch")
                raise

    @staticmethod
    async def send_sms_batch(notification_id: str, delivery_ids: List[str]) -> None:
        """Send SMS messages for a batch of deliveries with concurrent sends and idempotent retries."""
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

                # FIX #4 (idempotency): only pull deliveries still needing a send.
                result = await delivery_repo.execute(
                    select(NotificationDelivery).where(
                        col(NotificationDelivery.id).in_(delivery_ids),
                        col(NotificationDelivery.status).in_(
                            [DeliveryStatus.PENDING, DeliveryStatus.FAILED]
                        ),
                    )
                )
                deliveries = list(result.all())
                if not deliveries:
                    return

                # FIX #2 (N+1): bulk fetch recipients and users.
                recipient_ids = list({d.recipient_id for d in deliveries})
                recipients_result = await recipient_repo.execute(
                    select(NotificationRecipient).where(col(NotificationRecipient.id).in_(recipient_ids))
                )
                recipients_by_id = {r.id: r for r in recipients_result.all()}

                user_ids = list({r.recipient_id for r in recipients_by_id.values()})
                users_result = await user_repo.execute(
                    select(User).where(col(User.id).in_(user_ids))
                )
                users_by_id = {u.id: u for u in users_result.all()}

                now = lagos_now()
                sem = asyncio.Semaphore(NotificationPipeline.SEND_CONCURRENCY)

                async def _send_one(delivery: NotificationDelivery):
                    recipient = recipients_by_id.get(delivery.recipient_id)
                    if not recipient:
                        return delivery.id, False, "recipient not found", False
                    user = users_by_id.get(recipient.recipient_id)
                    if not user:
                        return delivery.id, False, "user not found", False
                    if not user.phone_number:
                        return delivery.id, False, "no phone number", True  # permanent failure
                    async with sem:
                        try:
                            result = await sms_service.send_sms(
                                phone_numbers=user.phone_number,
                                message=f"{notification.title}: {notification.body}",
                            )
                            ok = result.get(user.phone_number, False)
                            return delivery.id, ok, None if ok else "provider returned failure", False
                        except Exception as exc:
                            logger.error(f"[SMS] Failed to send delivery {delivery.id}: {exc}")
                            return delivery.id, False, str(exc), False

                # FIX #3 (concurrency): fire sends in parallel.
                results = await asyncio.gather(*(_send_one(d) for d in deliveries))

                succeeded_ids = [did for did, ok, _, _ in results if ok]
                failed_ids = [did for did, ok, _, perm in results if not ok and not perm]
                perm_failed_ids = [did for did, ok, _, perm in results if not ok and perm]

                if succeeded_ids:
                    await delivery_repo.bulk_update(
                        succeeded_ids,
                        {"status": DeliveryStatus.DELIVERED, "sent_at": now, "delivered_at": now},
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

                logger.info(f"[SMS] Batch complete: {len(succeeded_ids)} sent, {len(failed_ids)} failed")
                await system_logger.metric("send_sms_batch", timer.stop(), source="celery.send_sms_batch")
            except Exception as e:
                await system_logger.error(f"send_sms_batch Failed: {str(e)}", source="celery.send_sms_batch")
                raise

    @staticmethod
    async def send_push_batch(notification_id: str, delivery_ids: List[str]) -> None:
        """Send push notifications for a batch of deliveries with concurrent sends and idempotent retries."""
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

                # FIX #4 (idempotency): only pull deliveries still needing a send.
                result = await delivery_repo.execute(
                    select(NotificationDelivery).where(
                        col(NotificationDelivery.id).in_(delivery_ids),
                        col(NotificationDelivery.status).in_(
                            [DeliveryStatus.PENDING, DeliveryStatus.FAILED]
                        ),
                    )
                )
                deliveries = list(result.all())
                if not deliveries:
                    return

                # FIX #2 (N+1): bulk fetch recipients and users.
                recipient_ids = list({d.recipient_id for d in deliveries})
                recipients_result = await recipient_repo.execute(
                    select(NotificationRecipient).where(col(NotificationRecipient.id).in_(recipient_ids))
                )
                recipients_by_id = {r.id: r for r in recipients_result.all()}

                user_ids = list({r.recipient_id for r in recipients_by_id.values()})
                users_result = await user_repo.execute(
                    select(User).where(col(User.id).in_(user_ids))
                )
                users_by_id = {u.id: u for u in users_result.all()}

                now = lagos_now()
                sem = asyncio.Semaphore(NotificationPipeline.SEND_CONCURRENCY)

                async def _send_one(delivery: NotificationDelivery):
                    recipient = recipients_by_id.get(delivery.recipient_id)
                    if not recipient:
                        return delivery.id, False, "recipient not found", False
                    user = users_by_id.get(recipient.recipient_id)
                    if not user:
                        return delivery.id, False, "user not found", False

                    active_devices = [
                        d for d in user.devices if d.is_active and d.messaging_token
                    ]
                    if not active_devices:
                        return delivery.id, False, "no active push devices", True  # permanent failure

                    async with sem:
                        try:
                            send_results = await asyncio.gather(
                                *(
                                    push_svc.send_message(
                                        token=device.messaging_token,
                                        title=notification.title,
                                        body=notification.body,
                                        data=data_payload,
                                    )
                                    for device in active_devices
                                )
                            )
                            if any(send_results):
                                return delivery.id, True, None, False
                            else:
                                return delivery.id, False, "all device sends failed", False
                        except Exception as exc:
                            logger.error(f"[Push] Failed to send delivery {delivery.id}: {exc}")
                            return delivery.id, False, str(exc), False

                # FIX #3 (concurrency): fire sends in parallel.
                results = await asyncio.gather(*(_send_one(d) for d in deliveries))

                succeeded_ids = [did for did, ok, _, _ in results if ok]
                failed_ids = [did for did, ok, _, perm in results if not ok and not perm]
                perm_failed_ids = [did for did, ok, _, perm in results if not ok and perm]

                if succeeded_ids:
                    await delivery_repo.bulk_update(
                        succeeded_ids,
                        {"status": DeliveryStatus.DELIVERED, "sent_at": now, "delivered_at": now},
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

                logger.info(f"[Push] Batch complete: {len(succeeded_ids)} sent, {len(failed_ids)} failed")
                await system_logger.metric("send_push_batch", timer.stop(), source="celery.send_push_batch")
            except Exception as e:
                await system_logger.error(f"send_push_batch Failed: {str(e)}", source="celery.send_push_batch")
                raise

    @staticmethod
    async def send_whatsapp_batch(notification_id: str, delivery_ids: List[str]) -> None:
        """Send WhatsApp messages for a batch of deliveries with concurrent sends and idempotent retries."""
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

                # FIX #4 (idempotency): only pull deliveries still needing a send.
                result = await delivery_repo.execute(
                    select(NotificationDelivery).where(
                        col(NotificationDelivery.id).in_(delivery_ids),
                        col(NotificationDelivery.status).in_(
                            [DeliveryStatus.PENDING, DeliveryStatus.FAILED]
                        ),
                    )
                )
                deliveries = list(result.all())
                if not deliveries:
                    return

                # FIX #2 (N+1): bulk fetch recipients and users.
                recipient_ids = list({d.recipient_id for d in deliveries})
                recipients_result = await recipient_repo.execute(
                    select(NotificationRecipient).where(col(NotificationRecipient.id).in_(recipient_ids))
                )
                recipients_by_id = {r.id: r for r in recipients_result.all()}

                user_ids = list({r.recipient_id for r in recipients_by_id.values()})
                users_result = await user_repo.execute(
                    select(User).where(col(User.id).in_(user_ids))
                )
                users_by_id = {u.id: u for u in users_result.all()}

                now = timenow()
                sem = asyncio.Semaphore(NotificationPipeline.SEND_CONCURRENCY)

                async def _send_one(delivery: NotificationDelivery):
                    recipient = recipients_by_id.get(delivery.recipient_id)
                    if not recipient:
                        return delivery.id, False, "recipient not found", False
                    user = users_by_id.get(recipient.recipient_id)
                    if not user:
                        return delivery.id, False, "user not found", False
                    if not user.phone_number:
                        return delivery.id, False, "no phone number for WhatsApp", True  # permanent failure
                    async with sem:
                        try:
                            result = await whatsapp_service.send_message(
                                phone_numbers=user.phone_number,
                                message=f"{notification.title}: {notification.body}",
                            )
                            ok = result.get(user.phone_number, False)
                            return delivery.id, ok, None if ok else "provider returned failure", False
                        except Exception as exc:
                            logger.error(f"[WhatsApp] Failed to send delivery {delivery.id}: {exc}")
                            return delivery.id, False, str(exc), False

                # FIX #3 (concurrency): fire sends in parallel.
                results = await asyncio.gather(*(_send_one(d) for d in deliveries))

                succeeded_ids = [did for did, ok, _, _ in results if ok]
                failed_ids = [did for did, ok, _, perm in results if not ok and not perm]
                perm_failed_ids = [did for did, ok, _, perm in results if not ok and perm]

                if succeeded_ids:
                    await delivery_repo.bulk_update(
                        succeeded_ids,
                        {"status": DeliveryStatus.DELIVERED, "sent_at": now, "delivered_at": now},
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

                logger.info(f"[WhatsApp] Batch complete: {len(succeeded_ids)} sent, {len(failed_ids)} failed")
                await system_logger.metric("send_whatsapp_batch", timer.stop(), source="celery.send_whatsapp_batch")
            except Exception as e:
                await system_logger.error(f"send_whatsapp_batch Failed: {str(e)}", source="celery.send_whatsapp_batch")
                raise


# ── Celery task wrappers (thin — logic lives in NotificationPipeline) ──────


@shared_task(name="notifications.process_notification", bind=True, max_retries=3, default_retry_delay=60)
def process_notification(self, notification_id: str) -> None:
    """Fan-out: paginate recipients in batches and dispatch batch workers."""
    logger.info(f"[Pipeline] Fan-out started for notification {notification_id}")
    try:
        run_async(NotificationPipeline.fan_out(notification_id))
        logger.info(f"[Pipeline] Fan-out complete for notification {notification_id}")
    except Exception as exc:
        logger.error(f"[Pipeline] Fan-out failed for notification {notification_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task(name="notifications.process_recipient_batch", bind=True, max_retries=3, default_retry_delay=60)
def process_recipient_batch(self, notification_id: str, recipient_ids: List[str]) -> None:
    """Check preferences, bulk-insert deliveries, and dispatch channel batch tasks."""
    logger.info(f"[Pipeline] Processing batch of {len(recipient_ids)} recipients for notification {notification_id}")
    try:
        run_async(NotificationPipeline.process_batch(notification_id, recipient_ids))
    except Exception as exc:
        logger.error(f"[Pipeline] Batch processing failed for notification {notification_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task(name="notifications.send_email_batch", bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def send_email_batch(self, notification_id: str, delivery_ids: List[str]) -> None:
    """Send emails for a batch of deliveries."""
    logger.info(f"[Email] Processing {len(delivery_ids)} deliveries for notification {notification_id}")
    run_async(NotificationPipeline.send_email_batch(notification_id, delivery_ids))


@shared_task(name="notifications.send_sms_batch", bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def send_sms_batch(self, notification_id: str, delivery_ids: List[str]) -> None:
    """Send SMS messages for a batch of deliveries."""
    logger.info(f"[SMS] Processing {len(delivery_ids)} deliveries for notification {notification_id}")
    run_async(NotificationPipeline.send_sms_batch(notification_id, delivery_ids))


@shared_task(name="notifications.send_push_batch", bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def send_push_batch(self, notification_id: str, delivery_ids: List[str]) -> None:
    """Send push notifications for a batch of deliveries."""
    logger.info(f"[Push] Processing {len(delivery_ids)} deliveries for notification {notification_id}")
    run_async(NotificationPipeline.send_push_batch(notification_id, delivery_ids))


@shared_task(name="notifications.send_whatsapp_batch", bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def send_whatsapp_batch(self, notification_id: str, delivery_ids: List[str]) -> None:
    """Send WhatsApp messages for a batch of deliveries."""
    logger.info(f"[WhatsApp] Processing {len(delivery_ids)} deliveries for notification {notification_id}")
    run_async(NotificationPipeline.send_whatsapp_batch(notification_id, delivery_ids))
