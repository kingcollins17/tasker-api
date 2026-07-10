from typing import List, Dict, Union
from app.core.logging import logger


class WhatsAppService:
    """Service to handle WhatsApp communications (Mock implementation)."""

    async def send_message(
        self,
        phone_numbers: Union[str, List[str]],
        message: str,
    ) -> Dict[str, bool]:
        """Sends a WhatsApp message to one or more recipients.

        Args:
            phone_numbers: A single phone number or a list of phone numbers.
            message: The WhatsApp message text.

        Returns:
            Dict[str, bool]: Map of phone number to success status.
        """
        phones = [phone_numbers] if isinstance(phone_numbers, str) else phone_numbers

        results = {}

        for phone in phones:
            logger.info(
                f"[MOCK WHATSAPP] Sending WhatsApp to: {phone} | Message: {message}"
            )
            results[phone] = True

        return results
