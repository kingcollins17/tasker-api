from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class CloudMessagingService(ABC):
    """Abstract base class representing a Cloud Messaging Service (e.g. Firebase Cloud Messaging)."""

    @abstractmethod
    async def send_message(
        self,
        token: str,
        title: str,
        body: str,
        data: Optional[Dict[str, str]] = None
    ) -> bool:
        """Sends a notification message to a single device token.

        Args:
            token: The recipient device registration token.
            title: The notification title.
            body: The notification body text.
            data: Optional key-value payload to include in the message.

        Returns:
            bool: True if the message was sent successfully, False otherwise.
        """
        pass

    @abstractmethod
    async def send_multicast(
        self,
        tokens: List[str],
        title: str,
        body: str,
        data: Optional[Dict[str, str]] = None
    ) -> Dict[str, bool]:
        """Sends a notification message to multiple device tokens.

        Args:
            tokens: List of recipient device registration tokens.
            title: The notification title.
            body: The notification body text.
            data: Optional key-value payload to include in the message.

        Returns:
            Dict[str, bool]: Map of registration token to success status.
        """
        pass


class MockCloudMessagingService(CloudMessagingService):
    """Mock implementation of the CloudMessagingService interface."""

    async def send_message(
        self,
        token: str,
        title: str,
        body: str,
        data: Optional[Dict[str, str]] = None
    ) -> bool:
        """Simulates sending a notification to a single device token.

        Args:
            token: The registration token.
            title: The notification title.
            body: The notification body.
            data: Optional data payload.

        Returns:
            bool: True to simulate a successful send.
        """
        from app.core.logging import logger
        logger.info(
            f"[MOCK FCM] Sending message to: {token} | "
            f"Title: {title} | Body: {body} | Data: {data}"
        )
        return True

    async def send_multicast(
        self,
        tokens: List[str],
        title: str,
        body: str,
        data: Optional[Dict[str, str]] = None
    ) -> Dict[str, bool]:
        """Simulates sending a notification to multiple device tokens.

        Args:
            tokens: List of registration tokens.
            title: The notification title.
            body: The notification body.
            data: Optional data payload.

        Returns:
            Dict[str, bool]: Map of registration token to success status (all True).
        """
        from app.core.logging import logger
        logger.info(
            f"[MOCK FCM] Sending multicast to: {tokens} | "
            f"Title: {title} | Body: {body} | Data: {data}"
        )
        return {token: True for token in tokens}


# Dependency provider
def get_cloud_messaging_service() -> CloudMessagingService:
    """Dependency provider function for CloudMessagingService.

    Returns a MockCloudMessagingService instance.
    """
    return MockCloudMessagingService()
