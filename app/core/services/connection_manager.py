import asyncio
import json
from typing import Dict, Optional

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.core.logging import logger


class ConnectionManager:
    """Manages WebSocket connections mapped to user IDs.

    Each user can have at most one active WebSocket connection. If a user
    connects again, the previous connection is closed before the new one
    is registered.
    """

    def __init__(self) -> None:
        self._connections: Dict[str, WebSocket] = {}

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        """Accept a WebSocket and register it for the given user.

        If the user already has an active connection, it is closed first
        (only one connection per user is allowed).
        """
        # Close existing connection if any
        existing = self._connections.get(user_id)
        if existing is not None:
            try:
                await existing.close(code=4000, reason="Replaced by new connection")
            except Exception:
                pass

        await websocket.accept()
        self._connections[user_id] = websocket
        logger.info(f"[WS] User {user_id} connected. Active connections: {len(self._connections)}")

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        """Remove the WebSocket connection for the given user.

        Only removes if the stored connection matches the provided websocket
        (guards against race conditions with reconnection).
        """
        current = self._connections.get(user_id)
        if current is websocket:
            del self._connections[user_id]
            logger.info(f"[WS] User {user_id} disconnected. Active connections: {len(self._connections)}")

    async def send_to_user(self, user_id: str, message: dict) -> bool:
        """Send a JSON message to a user's active WebSocket connection.

        Args:
            user_id: Target user ID.
            message: JSON-serializable dict to send.

        Returns:
            True if the message was sent, False if the user has no
            connection on this instance.
        """
        websocket = self._connections.get(user_id)
        if websocket is None:
            return False

        try:
            await websocket.send_json(message)
            return True
        except Exception:
            # Connection is broken — clean it up
            self.disconnect(user_id, websocket)
            return False

    async def broadcast(self, message: dict) -> None:
        """Send a JSON message to all connected users.

        Broken connections are cleaned up automatically.
        """
        # Iterate over a snapshot to allow mutation during iteration
        stale: list = []
        for user_id, websocket in list(self._connections.items()):
            try:
                await websocket.send_json(message)
            except Exception:
                stale.append((user_id, websocket))

        for user_id, websocket in stale:
            self.disconnect(user_id, websocket)

    def is_connected(self, user_id: str) -> bool:
        """Check if a user has an active WebSocket connection on this instance."""
        return user_id in self._connections

    @property
    def active_count(self) -> int:
        """Number of active WebSocket connections."""
        return len(self._connections)


# Module-level singleton
_connection_manager: Optional[ConnectionManager] = None


def get_connection_manager() -> ConnectionManager:
    """Returns the singleton ConnectionManager instance."""
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ConnectionManager()
    return _connection_manager
