from typing import List, Dict, Union
from app.core.logging import logger

class SMSService:
    """Service to handle SMS communications (Mock implementation)."""

    async def send_sms(
        self,
        phone_numbers: Union[str, List[str]],
        message: str
    ) -> Dict[str, bool]:
        """Sends SMS to one or more recipients.

        Args:
            phone_numbers: A single phone number or a list of phone numbers.
            message: The SMS message text.

        Returns:
            Dict[str, bool]: Map of phone number to success status.
        """
        # Normalize to list of phone numbers
        phones = [phone_numbers] if isinstance(phone_numbers, str) else phone_numbers
        
        results = {}
        
        for phone in phones:
            logger.info(
                f"[MOCK SMS] Sending SMS to: {phone} | Message: {message}"
            )
            results[phone] = True
            
        return results



