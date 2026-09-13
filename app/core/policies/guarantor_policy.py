"""GuarantorPolicy engine defining configurable verification submission limits and resubmission rules."""

from app.core.models.users import VerificationStatus


class GuarantorPolicy:
    """Policy engine enforcing max attempt thresholds and validation rules for Guarantor submissions."""

    MAX_ATTEMPTS: int = 3
    PENDING_STATUSES = {VerificationStatus.PENDING, VerificationStatus.UNDER_REVIEW}

    @classmethod
    def can_resubmit(cls, attempt_count: int) -> bool:
        """Determines if resubmission is allowed based on the number of prior attempts."""
        return attempt_count < cls.MAX_ATTEMPTS

    @classmethod
    def is_max_attempts_reached(cls, attempt_count: int) -> bool:
        """Determines if the maximum number of guarantor submission attempts has been reached."""
        return attempt_count >= cls.MAX_ATTEMPTS
