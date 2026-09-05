"""Deterministic Retry Policy with Exponential Backoff and Stopping Conditions."""

import logging
import math
from typing import Optional, Set, Tuple

from app.config import settings

logger = logging.getLogger("iro.execution.retry_policy")

# Canonical transient failure codes eligible for automated retry
RETRYABLE_ERROR_CODES: Set[str] = {
    "GATEWAY_TIMEOUT",
    "BANK_SYSTEM_BUSY",
    "NETWORK_ERROR",
    "ROUTE_DEGRADATION",
    "ACQUIRER_DOWNTIME",
    "TIMEOUT",
    "DOWNSTREAM_503",
    "CONNECTION_RESET",
}

# Permanent failure codes that must NEVER be automatically retried
NON_RETRYABLE_ERROR_CODES: Set[str] = {
    "ACCOUNT_CLOSED",
    "BLOCKED_CARD",
    "INVALID_ACCOUNT",
    "FRAUD_SUSPECTED",
    "CARD_EXPIRED",
    "INVALID_CVV",
    "INVALID_PIN",
    "LIMIT_EXCEEDED",
    "INSUFFICIENT_FUNDS",  # Customer intervention required, not blind retry
}


class RecoveryRetryPolicy:
    """Calculates backoff intervals and evaluates stopping conditions for retries."""

    def __init__(
        self,
        base_backoff_sec: Optional[float] = None,
        max_backoff_sec: Optional[float] = None,
    ):
        self.base_backoff_sec = base_backoff_sec or settings.RETRY_EXPONENTIAL_BASE_SEC
        self.max_backoff_sec = max_backoff_sec or settings.RETRY_MAX_BACKOFF_SEC

    def calculate_backoff(self, attempt_number: int) -> float:
        """Calculate deterministic exponential backoff in seconds.

        Formula: min(base * (2 ^ (attempt - 1)), max_backoff)
        Example with base=1.0: attempt 1 -> 1.0s, attempt 2 -> 2.0s, attempt 3 -> 4.0s
        """
        if attempt_number <= 1:
            backoff = self.base_backoff_sec
        else:
            backoff = self.base_backoff_sec * (2.0 ** (attempt_number - 1))
        return min(backoff, self.max_backoff_sec)

    def is_retryable(self, error_code: Optional[str]) -> bool:
        """Check if an error code is eligible for automated bank retry."""
        if not error_code:
            return False
        code = error_code.upper().strip()
        if code in NON_RETRYABLE_ERROR_CODES:
            return False
        return code in RETRYABLE_ERROR_CODES

    def evaluate_next_retry(
        self,
        current_attempt: int,
        max_retries: int,
        error_code: Optional[str],
        elapsed_seconds: float = 0.0,
        max_window_sec: float = 3600.0,
    ) -> Tuple[bool, float, Optional[str]]:
        """Evaluate whether a next retry should be scheduled, its backoff, and stop reason if halted.

        Returns:
            (should_retry, backoff_seconds, stop_reason)
        """
        # 1. Non-retryable error check
        if not self.is_retryable(error_code):
            return False, 0.0, "NON_RETRYABLE_FAILURE"

        # 2. Maximum retries stopping condition
        if current_attempt >= max_retries:
            return False, 0.0, "MAX_RETRIES_EXCEEDED"

        # 3. Maximum SLA recovery window stopping condition
        if elapsed_seconds >= max_window_sec:
            return False, 0.0, "RECOVERY_WINDOW_EXPIRED"

        # 4. Schedule next retry with exponential backoff
        backoff = self.calculate_backoff(current_attempt)
        return True, backoff, None
