from typing import List, Optional, Tuple

from fastapi import Depends
from sqlmodel import select, func, col

from app.core.logging import log_error, logger
from app.core.models.notifications import (
    Notification,
    NotificationDelivery,
    NotificationPreference,
    NotificationRecipient,
    RecipientStatus,
)
from app.core.models.users import User
from app.core.repository import GetRepository, QueryOptions, Repository
from app.core.utils.datetime_helper import utc_now
from app.features.notifications.schemas import (
    BulkUpdatePreferences,
    CreateNotification,
)


class NotificationService:
    """API-facing service for notification creation, reads, and preference management.

    Delivery processing has been moved to the distributed Celery task pipeline
    (see tasks.py) for scalability.
    """

    def __init__(
        self,
        notification_repo: Repository[Notification],
        recipient_repo: Repository[NotificationRecipient],
        delivery_repo: Repository[NotificationDelivery],
        preference_repo: Repository[NotificationPreference],
        user_repo: Repository[User],
    ):
        self.notification_repo = notification_repo
        self.recipient_repo = recipient_repo
        self.delivery_repo = delivery_repo
        self.preference_repo = preference_repo
        self.user_repo = user_repo

    # ── Create ───────────────────────────────────────────────────────────

    @log_error()
    async def create_notification(
        self,
        schema: CreateNotification,
        created_by: Optional[str] = None,
    ) -> Notification:
        """Create a Notification + NotificationRecipient rows and enqueue delivery via Celery."""

        notification = Notification(
            type=schema.type,
            title=schema.title,
            body=schema.body,
            data=schema.data,
            channels=[c.value for c in schema.channels] if schema.channels else None,
            priority=schema.priority,
            created_by=created_by,
            scheduled_for=schema.scheduled_for,
            expires_at=schema.expires_at,
        )
        notification = await self.notification_repo.add(notification)

        # Create recipient rows
        for user_id in schema.recipient_ids:
            recipient = NotificationRecipient(
                notification_id=notification.id,
                recipient_id=user_id,
            )
            await self.recipient_repo.add(recipient)

        # Refresh to load relationships
        await self.notification_repo.refresh(notification)

        # Dispatch via Celery fan-out task (returns immediately)
        from app.features.notifications.tasks import process_notification

        try:
            # pyrefly: ignore [not-callable]
            process_notification.delay(notification.id)  # pyright: ignore [reportCallIssue]
        except Exception as e:
            logger.error(f"Failed to enqueue notification {notification.id} for processing: {e}")

        return notification

    # ── Read operations ──────────────────────────────────────────────────

    @log_error()
    async def get_user_notifications(
        self,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[dict], int]:
        """Return paginated notifications for a user, newest first."""

        # Count total
        count_stmt = (
            select(func.count())
            .select_from(NotificationRecipient)
            .where(col(NotificationRecipient.recipient_id) == user_id)
        )
        count_result = await self.recipient_repo.execute(count_stmt)
        total = count_result.one()

        # Fetch page
        stmt = (
            select(NotificationRecipient, Notification)
            .join(
                Notification,
                col(NotificationRecipient.notification_id) == col(Notification.id),
            )
            .where(col(NotificationRecipient.recipient_id) == user_id)
            .order_by(col(NotificationRecipient.created_at).desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.recipient_repo.execute(stmt)
        rows = list(result.all())

        items = []
        for recip, notif in rows:
            items.append(
                {
                    "notification_id": notif.id,
                    "recipient_record_id": recip.id,
                    "type": notif.type,
                    "title": notif.title,
                    "body": notif.body,
                    "data": notif.data,
                    "priority": notif.priority,
                    "status": recip.status,
                    "read_at": recip.read_at,
                    "created_at": recip.created_at,
                }
            )

        return items, total

    @log_error()
    async def get_unread_count(self, user_id: str) -> int:
        """Return the number of unread notifications for a user."""
        stmt = (
            select(func.count())
            .select_from(NotificationRecipient)
            .where(col(NotificationRecipient.recipient_id) == user_id)
            .where(col(NotificationRecipient.status) != RecipientStatus.READ)
        )
        result = await self.recipient_repo.execute(stmt)
        return result.one()

    @log_error()
    async def get_notification_counts(self, user_id: str) -> dict:
        """Return the number of read and unread notifications for a user."""
        unread_stmt = (
            select(func.count())
            .select_from(NotificationRecipient)
            .where(col(NotificationRecipient.recipient_id) == user_id)
            .where(col(NotificationRecipient.status) != RecipientStatus.READ)
        )
        unread_result = await self.recipient_repo.execute(unread_stmt)
        unread_count = unread_result.one()

        read_stmt = (
            select(func.count())
            .select_from(NotificationRecipient)
            .where(col(NotificationRecipient.recipient_id) == user_id)
            .where(col(NotificationRecipient.status) == RecipientStatus.READ)
        )
        read_result = await self.recipient_repo.execute(read_stmt)
        read_count = read_result.one()

        return {"read": read_count, "unread": unread_count}

    @log_error()
    async def mark_as_read(self, user_id: str, notification_ids: List[str]) -> int:
        """Mark notifications as read for a specific user. Returns number of rows updated."""
        now = utc_now()
        stmt = (
            select(NotificationRecipient)
            .where(col(NotificationRecipient.recipient_id) == user_id)
            .where(col(NotificationRecipient.notification_id).in_(notification_ids))
            .where(col(NotificationRecipient.status) != RecipientStatus.READ)
        )
        result = await self.recipient_repo.execute(stmt)
        rows = list(result.all())

        for recip in rows:
            await self.recipient_repo.update(
                recip.id,
                {
                    "status": RecipientStatus.READ,
                    "read_at": now,
                },
            )

        return len(rows)

    @log_error()
    async def get_delivery_status(
        self, notification_id: str
    ) -> List[NotificationDelivery]:
        """Return all delivery rows for a notification (across all its recipients)."""
        stmt = (
            select(NotificationDelivery)
            .join(
                NotificationRecipient,
                col(NotificationDelivery.recipient_id) == col(NotificationRecipient.id),
            )
            .where(col(NotificationRecipient.notification_id) == notification_id)
        )
        result = await self.delivery_repo.execute(stmt)
        return list(result.all())

    # ── Preferences ──────────────────────────────────────────────────────

    @log_error()
    async def get_preferences(self, user_id: str) -> List[NotificationPreference]:
        """Return all notification preferences for a user."""
        return await self.preference_repo.get_all(
            QueryOptions(filters={"user_id": user_id})
        )

    @log_error()
    async def update_preferences(
        self,
        user_id: str,
        schema: BulkUpdatePreferences,
    ) -> List[NotificationPreference]:
        """Upsert notification preferences for a user."""
        results: List[NotificationPreference] = []

        for pref in schema.preferences:
            # Check if preference already exists
            existing = await self.preference_repo.get_all(
                QueryOptions(
                    filters={
                        "user_id": user_id,
                        "notification_type": pref.notification_type,
                        "channel": pref.channel,
                    }
                )
            )

            if existing:
                updated = await self.preference_repo.update(
                    existing[0].id, {"enabled": pref.enabled}
                )
                if updated:
                    results.append(updated)
            else:
                new_pref = NotificationPreference(
                    user_id=user_id,
                    notification_type=pref.notification_type,
                    channel=pref.channel,
                    enabled=pref.enabled,
                )
                new_pref = await self.preference_repo.add(new_pref)
                results.append(new_pref)

        return results


# ── Dependency provider ──────────────────────────────────────────────────────


def get_notification_service(
    notification_repo: Repository[Notification] = Depends(GetRepository(Notification)),
    recipient_repo: Repository[NotificationRecipient] = Depends(
        GetRepository(NotificationRecipient)
    ),
    delivery_repo: Repository[NotificationDelivery] = Depends(
        GetRepository(NotificationDelivery)
    ),
    preference_repo: Repository[NotificationPreference] = Depends(
        GetRepository(NotificationPreference)
    ),
    user_repo: Repository[User] = Depends(GetRepository(User)),
) -> NotificationService:
    """FastAPI dependency provider for NotificationService."""
    return NotificationService(
        notification_repo=notification_repo,
        recipient_repo=recipient_repo,
        delivery_repo=delivery_repo,
        preference_repo=preference_repo,
        user_repo=user_repo,
    )
