from datetime import timedelta
from typing import List, Optional

from app.core.models.tasks import DispatchAttemptStatus


class DispatchPolicy:
    """Policy engine defining configurable dispatch retry limits, retry delays,
    and provider exclusion rules.
    """

    AUTO_DISPATCH_MAX: int = 5
    MANUAL_DISPATCH_MAX: int = 3

    # Delays for retries: 2m, 5m, 10m, 20m, 30m
    RETRY_DELAYS_SECONDS: List[int] = [120, 300, 600, 1200, 1800]

    # Stale claim recovery threshold
    STALE_CLAIM_TIMEOUT_SECONDS: int = 300  # 5 minutes

    @classmethod
    def can_auto_retry(cls, auto_dispatch_count: int) -> bool:
        """Determines if automatic retry is permitted based on attempt count."""
        return auto_dispatch_count < cls.AUTO_DISPATCH_MAX

    @classmethod
    def can_manual_redispatch(cls, manual_dispatch_count: int) -> bool:
        """Determines if manual redispatch is permitted based on attempt count."""
        return manual_dispatch_count < cls.MANUAL_DISPATCH_MAX

    @classmethod
    def get_next_retry_delay(cls, auto_dispatch_count: int) -> timedelta:
        """Returns delay duration for next automatic retry based on zero-indexed auto_dispatch_count."""
        index = min(max(0, auto_dispatch_count), len(cls.RETRY_DELAYS_SECONDS) - 1)
        return timedelta(seconds=cls.RETRY_DELAYS_SECONDS[index])

    @classmethod
    def should_exclude_provider_status(cls, status: Optional[DispatchAttemptStatus]) -> bool:
        """Determines if a provider with the given attempt status should be excluded from future matches.
        By default, DECLINED, TIMEOUT, and CANCELLED attempts are excluded.
        """
        if status is None:
            return False
        return status in {
            DispatchAttemptStatus.DECLINED,
            DispatchAttemptStatus.TIMEOUT,
            DispatchAttemptStatus.CANCELLED,
        }
