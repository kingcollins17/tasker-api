"""Server-Sent Events (SSE) connection manager.

Maps each user ID to a single ``SSEConnection``. If a user connects
again, the previous connection is closed before the new one is registered.

The manager exposes two public delivery methods:

- ``send(user_id, payload)`` — push to the user's local connection.
- ``broadcast(payload)`` — push to all locally connected users.

Redis Pub/Sub publishing is handled externally (e.g. in Celery tasks or
the notification service). The pubsub listener in ``notification_pubsub``
calls these methods to perform local delivery.

Scalability:
    A periodic cleanup task runs every ``CLEANUP_INTERVAL_SECONDS`` to
    remove stale connections that haven't received a heartbeat ack within
    ``STALE_TIMEOUT_SECONDS``. This prevents memory leaks from abandoned
    connections at scale.
"""

import asyncio
import json
import time
from typing import TYPE_CHECKING, AsyncGenerator, Dict, Optional

from app.core.logging import logger

if TYPE_CHECKING:
    from app.core.services.cache import CacheService

# Redis Pub/Sub channels — must match the values in notification_pubsub.py.
NOTIFICATION_CHANNEL = "in_app_notifications"
BROADCAST_CHANNEL = "in_app_notifications_broadcast"

# Connections idle longer than this (in seconds) are considered stale.
STALE_TIMEOUT_SECONDS = 120

# How long subscribe() waits for messages before looping to check conn.closed.
RECEIVE_TIMEOUT_SECONDS = 60.0

# How often the cleanup task runs (in seconds).
CLEANUP_INTERVAL_SECONDS = 60 * 5


class SSEConnection:
    """Lightweight object representing a single SSE connection.

    Uses an ``asyncio.Event`` for signaling and a plain list as a
    message buffer. Tracks last activity time for stale-connection
    detection.
    """

    __slots__ = ("_event", "_messages", "closed", "last_active")

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._messages: list = []
        self.closed = False
        self.last_active: float = time.monotonic()

    def push(self, payload: dict) -> None:
        """Buffer a message and wake the waiting generator."""
        self._messages.append(payload)
        self._event.set()

    async def receive(self, timeout: float = 30.0) -> Optional[list]:
        """Wait up to *timeout* seconds for messages.

        Returns a list of buffered messages, or ``None`` on timeout
        (used by the generator to emit a heartbeat).
        """
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        # Drain the buffer
        self._event.clear()
        messages = self._messages[:]
        self._messages.clear()
        return messages

    def touch(self) -> None:
        """Update the last-active timestamp (called on each heartbeat)."""
        self.last_active = time.monotonic()

    def close(self) -> None:
        """Signal the generator to stop."""
        self.closed = True
        self._event.set()


class SSEManager:
    """Manages SSE connections mapped to user IDs.

    Each user can have at most one active SSE connection. If a user
    connects again, the previous connection is closed before the new
    one is registered.

    A background cleanup task removes stale connections periodically.
    """

    def __init__(self) -> None:
        self._connections: Dict[str, SSEConnection] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._start_cleanup()

    # ── Connection lifecycle ─────────────────────────────────────────────

    def connect(self, user_id: str) -> SSEConnection:
        """Register a new SSE connection for *user_id*.

        If the user already has an active connection, it is closed first
        (only one connection per user is allowed).

        Returns the ``SSEConnection`` that the SSE endpoint should
        consume from via :meth:`subscribe`.
        """
        existing = self._connections.get(user_id)
        if existing is not None:
            existing.close()

        conn = SSEConnection()
        self._connections[user_id] = conn
        logger.info(
            f"[SSE] User {user_id} connected. "
            f"Total connections: {len(self._connections)}"
        )
        return conn

    def disconnect(self, user_id: str, conn: SSEConnection) -> None:
        """Remove the SSE connection for *user_id*.

        Only removes if the stored connection matches the provided one
        (guards against race conditions with reconnection).
        """
        current = self._connections.get(user_id)
        if current is conn:
            del self._connections[user_id]
            logger.info(
                f"[SSE] User {user_id} disconnected. "
                f"Total connections: {len(self._connections)}"
            )

    # ── Delivery ─────────────────────────────────────────────────────────

    def send(self, user_id: str, payload: dict) -> bool:
        """Push *payload* to the user's local SSE connection.

        Returns ``True`` if the user has an active connection.
        """
        conn = self._connections.get(user_id)
        if conn is None:
            return False
        conn.push(payload)
        return True

    def broadcast(self, payload: dict) -> None:
        """Push *payload* to every locally connected user."""
        for conn in self._connections.values():
            conn.push(payload)

    # ── Redis publishing ─────────────────────────────────────────────────

    @staticmethod
    async def publish(cache: "CacheService", user_id: str, notification: dict) -> int:
        """Publish a notification targeting a specific user via Redis Pub/Sub.

        The pubsub listener on every instance picks this up and calls
        :meth:`send` for local delivery.

        Args:
            cache: The CacheService instance to publish through.
            user_id: Target user ID.
            notification: JSON-serializable notification payload.

        Returns:
            Number of Redis subscribers that received the message.
        """
        message = json.dumps({"user_id": user_id, "notification": notification})
        return await cache.publish(NOTIFICATION_CHANNEL, message)

    @staticmethod
    async def publish_broadcast(cache: "CacheService", notification: dict) -> int:
        """Publish a broadcast notification via Redis Pub/Sub.

        Every connected user on every instance will receive the event.

        Args:
            cache: The CacheService instance to publish through.
            notification: JSON-serializable notification payload.

        Returns:
            Number of Redis subscribers that received the message.
        """
        message = json.dumps({"broadcast": True, "notification": notification})
        return await cache.publish(BROADCAST_CHANNEL, message)

    # ── Async generator for StreamingResponse ────────────────────────────

    async def subscribe(
        self, user_id: str, conn: SSEConnection
    ) -> AsyncGenerator[dict, None]:
        """Async generator yielding notification payloads as dicts.

        The endpoint is responsible for SSE formatting (``data: …\\n\\n``).

        The generator exits when the client disconnects (detected by
        ``asyncio.CancelledError``) or when the connection is closed.
        """
        try:
            while not conn.closed:
                messages = await conn.receive(timeout=RECEIVE_TIMEOUT_SECONDS)
                if messages is None:
                    # No data — just loop to re-check conn.closed
                    continue
                if conn.closed:
                    break
                conn.touch()
                for msg in messages:
                    yield msg
        except asyncio.CancelledError:
            pass
        finally:
            self.disconnect(user_id, conn)

    # ── Stale connection cleanup ─────────────────────────────────────────

    def _start_cleanup(self) -> None:
        """Start the periodic cleanup background task."""
        try:
            loop = asyncio.get_running_loop()
            self._cleanup_task = loop.create_task(self._cleanup_loop())
        except RuntimeError:
            # No running loop yet (e.g. during import) — the singleton is
            # lazily created on first access inside a running loop, so this
            # branch is unlikely but safe to ignore.
            pass

    async def _cleanup_loop(self) -> None:
        """Periodically close and remove stale connections."""
        while True:
            try:
                await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
                self._evict_stale()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[SSE] Error in cleanup loop: {e}")

    def _evict_stale(self) -> None:
        """Close connections that have been idle beyond the stale timeout."""
        now = time.monotonic()
        stale_ids: list = []
        for user_id, conn in self._connections.items():
            if conn.closed or (now - conn.last_active) > STALE_TIMEOUT_SECONDS:
                stale_ids.append(user_id)

        for user_id in stale_ids:
            conn = self._connections.pop(user_id, None)
            if conn is not None:
                conn.close()

        if stale_ids:
            logger.info(
                f"[SSE] Cleanup evicted {len(stale_ids)} stale connection(s). "
                f"Remaining: {len(self._connections)}"
            )

    # ── Helpers ──────────────────────────────────────────────────────────

    def is_connected(self, user_id: str) -> bool:
        """Check if a user has an active SSE connection."""
        return user_id in self._connections

    @property
    def active_connections(self) -> int:
        """Total number of active SSE connections."""
        return len(self._connections)

    def shutdown(self) -> None:
        """Cancel the cleanup task and close all connections."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None
        for conn in self._connections.values():
            conn.close()
        self._connections.clear()


# ── Module-level singleton ───────────────────────────────────────────────────

_sse_manager: Optional[SSEManager] = None


def get_sse_manager() -> SSEManager:
    """Returns the singleton SSEManager instance."""
    global _sse_manager
    if _sse_manager is None:
        _sse_manager = SSEManager()
    return _sse_manager
