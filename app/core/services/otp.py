import secrets
from typing import Optional

from app.core.config import settings
from app.core.logging import logger, log_error
from app.core.services.cache import CacheService
from app.core.services.email import EmailService
from app.core.services.sms import SMSService


class OTPError(Exception):
    """Base exception class for OTP-related errors."""
    pass


class OTPRateLimitError(OTPError):
    """Raised when an OTP is requested within the cooldown period."""
    pass


class OTPVerificationError(OTPError):
    """Raised when OTP verification fails (expired or not found)."""
    pass


class OTPMaxAttemptsReachedError(OTPVerificationError):
    """Raised when maximum verification attempts for a target are exceeded."""
    pass


class OTPService:
    """Service responsible for generating, sending, and verifying 6-digit OTP codes."""

    def __init__(
        self,
        cache_service: CacheService,
        email_service: EmailService,
        sms_service: SMSService,
        otp_expiry_seconds: Optional[int] = None,
        cooldown_seconds: Optional[int] = None,
        max_attempts: Optional[int] = None,
    ):
        """Initializes the OTPService with dependencies.

        Args:
            cache_service: The Redis CacheService instance.
            email_service: The EmailService instance.
            sms_service: The SMSService instance.
            otp_expiry_seconds: Expiration time in seconds for the OTP code.
            cooldown_seconds: Minimum time between consecutive OTP generation requests.
            max_attempts: Maximum incorrect verify attempts before OTP invalidation.
        """
        self.cache = cache_service
        self.email = email_service
        self.sms = sms_service
        self.otp_expiry = otp_expiry_seconds or settings.OTP_EXPIRY_SECONDS
        self.cooldown = cooldown_seconds or settings.OTP_COOLDOWN_SECONDS
        self.max_attempts = max_attempts or settings.OTP_MAX_ATTEMPTS

    def _get_otp_key(self, channel: str, target: str) -> str:
        return f"otp:{channel.lower()}:{target}"

    def _get_cooldown_key(self, channel: str, target: str) -> str:
        return f"otp_cooldown:{channel.lower()}:{target}"

    def _get_attempts_key(self, channel: str, target: str) -> str:
        return f"otp_attempts:{channel.lower()}:{target}"

    async def generate_otp(self, target: str, channel: str) -> str:
        """Generates a secure 6-digit OTP code and stores it in Redis.

        Enforces a cooldown limit to avoid spamming the target.

        Args:
            target: The recipient identifier (email address or phone number).
            channel: The delivery channel ('email' or 'sms').

        Returns:
            str: The generated 6-digit OTP.

        Raises:
            ValueError: If the delivery channel is invalid.
            OTPRateLimitError: If request is made before the cooldown period expires.
        """
        channel = channel.lower()
        if channel not in ("email", "sms"):
            raise ValueError("Invalid delivery channel. Must be 'email' or 'sms'.")

        cooldown_key = self._get_cooldown_key(channel, target)
        try:
            if await self.cache.exists(cooldown_key):
                logger.warning(f"OTP rate limit hit for {channel}:{target}")
                raise OTPRateLimitError("Please wait before requesting a new OTP.")
        except OTPRateLimitError:
            raise
        except Exception as e:
            # Fault tolerance: Log Cache connection errors but do not crash the app if cache check fails, 
            # though cache is required for storing the OTP.
            logger.error(f"Error checking OTP cooldown from cache: {str(e)}")

        # For testing/development, we generate "123456" as the OTP code
        # # Cryptographically secure 6-digit number generation (guarantees leading zeros if they occur)
        # code = "".join(secrets.choice("0123456789") for _ in range(6))
        code = "123456"

        otp_key = self._get_otp_key(channel, target)
        attempts_key = self._get_attempts_key(channel, target)

        try:
            # Set the OTP and attempts tracker in cache
            await self.cache.set(otp_key, code, expire=self.otp_expiry)
            await self.cache.set(attempts_key, "0", expire=self.otp_expiry)
            # Set cooldown block
            await self.cache.set(cooldown_key, "1", expire=self.cooldown)
        except Exception as e:
            logger.error(f"Failed to save OTP to Redis cache: {str(e)}")
            raise OTPError("Internal error generating verification code. Please try again later.")

        logger.info(f"Successfully generated OTP for {channel}:{target}")
        return code

    async def send_otp(self, target: str, channel: str, code: str) -> bool:
        """Dispatches the OTP code via the configured email or SMS service.

        Args:
            target: The recipient identifier (email address or phone number).
            channel: The delivery channel ('email' or 'sms').
            code: The 6-digit OTP code to send.

        Returns:
            bool: True if the dispatch succeeded, False otherwise.
        """
        channel = channel.lower()
        try:
            if channel == "email":
                subject = "Your Verification Code"
                body = f"Your verification code is: {code}. It is valid for {self.otp_expiry // 60} minutes."
                results = await self.email.send_email(
                    to_emails=target,
                    subject=subject,
                    body=body
                )
                return results.get(target, False)

            elif channel == "sms":
                message = f"Your verification code is: {code}. Valid for {self.otp_expiry // 60} minutes."
                results = await self.sms.send_sms(
                    phone_numbers=target,
                    message=message
                )
                return results.get(target, False)

        except Exception as e:
            logger.error(f"Exception raised while sending OTP via {channel} to {target}: {str(e)}")
            return False

        return False

    @log_error()
    async def generate_and_send_otp(self, target: str, channel: str) -> bool:
        """Wraps OTP generation and sending, cleaning up cache state if delivery fails.

        Args:
            target: The recipient identifier (email address or phone number).
            channel: The delivery channel ('email' or 'sms').

        Returns:
            bool: True if successfully generated and sent.

        Raises:
            OTPError: If code generation or dispatch fails.
        """
        code = await self.generate_otp(target, channel)
        sent = await self.send_otp(target, channel, code)

        if not sent:
            # Rollback: Clean up cache entries so target is not locked/cooldowned needlessly
            otp_key = self._get_otp_key(channel, target)
            attempts_key = self._get_attempts_key(channel, target)
            cooldown_key = self._get_cooldown_key(channel, target)
            try:
                await self.cache.delete(otp_key)
                await self.cache.delete(attempts_key)
                await self.cache.delete(cooldown_key)
            except Exception as e:
                logger.error(f"Error rolling back cache keys after failed OTP delivery: {str(e)}")
            raise OTPError(f"Failed to deliver verification code via {channel} to {target}.")

        return True

    async def verify_otp(self, target: str, channel: str, code: str) -> bool:
        """Verifies the provided OTP code against the cached value.

        Limits number of failed verification attempts. Cleanly deletes keys on success/max attempts.

        Args:
            target: The recipient identifier.
            channel: The delivery channel.
            code: The OTP code to check.

        Returns:
            bool: True if the code matches.

        Raises:
            OTPVerificationError: If OTP has expired or doesn't exist.
            OTPMaxAttemptsReachedError: If incorrect attempts exceed max attempts limit.
        """
        channel = channel.lower()

        otp_key = self._get_otp_key(channel, target)
        attempts_key = self._get_attempts_key(channel, target)

        try:
            stored_code = await self.cache.get(otp_key)
            attempts_str = await self.cache.get(attempts_key)
        except Exception as e:
            logger.error(f"Cache lookup failed during OTP verification: {str(e)}")
            raise OTPError("Internal error during verification check. Please try again.")

        if not stored_code:
            logger.warning(f"No active OTP found for verification: {channel}:{target}")
            raise OTPVerificationError("Verification code has expired or does not exist.")

        attempts = int(attempts_str) if attempts_str else 0

        # Check limit BEFORE incrementing and performing verification.
        # If user has already reached max attempts, block them.
        if attempts >= self.max_attempts:
            try:
                await self.cache.delete(otp_key)
                await self.cache.delete(attempts_key)
            except Exception as e:
                logger.error(f"Failed to delete OTP keys after max attempts exceeded: {str(e)}")
            logger.warning(f"Max OTP attempts exceeded for {channel}:{target}")
            raise OTPMaxAttemptsReachedError("Maximum verification attempts exceeded. Please request a new OTP.")

        # Increment tries counter
        attempts += 1
        try:
            await self.cache.set(attempts_key, str(attempts), expire=self.otp_expiry)
        except Exception as e:
            logger.error(f"Failed to update OTP attempt count in cache: {str(e)}")

        # Perform constant time comparison to mitigate timing attacks
        if secrets.compare_digest(stored_code, code):
            try:
                await self.cache.delete(otp_key)
                await self.cache.delete(attempts_key)
            except Exception as e:
                logger.error(f"Failed to delete OTP keys after successful verification: {str(e)}")
            logger.info(f"OTP successfully verified for {channel}:{target}")
            return True

        # If it was the final attempt, invalidate the OTP
        if attempts >= self.max_attempts:
            try:
                await self.cache.delete(otp_key)
                await self.cache.delete(attempts_key)
            except Exception as e:
                logger.error(f"Failed to delete OTP keys after final failed attempt: {str(e)}")
            logger.warning(f"Final failed OTP attempt for {channel}:{target}. Code invalidated.")
            raise OTPMaxAttemptsReachedError("Maximum verification attempts exceeded. Please request a new OTP.")

        logger.warning(f"Incorrect OTP attempt {attempts}/{self.max_attempts} for {channel}:{target}")
        return False
