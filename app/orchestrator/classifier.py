"""Deterministic Failure Classifier mapping error codes and failure categories to retryability classes."""

import logging
from typing import Dict, Set

from app.models.enums import FailureCategory, RetryabilityClass

logger = logging.getLogger(__name__)


class DeterministicFailureClassifier:
    """
    100% deterministic classifier.
    Categorizes incoming payment failures into RetryabilityClass without LLM guessing.
    """

    KNOWN_RETRYABLE_CODES: Set[str] = {
        "GATEWAY_TIMEOUT",
        "UPI_GATEWAY_TIMEOUT",
        "BANK_TIMEOUT",
        "NETWORK_ERROR",
        "NETWORK_TIMEOUT",
        "RATE_LIMIT_EXCEEDED",
        "BANK_SYSTEM_BUSY",
        "SWITCH_UNAVAILABLE",
        "SWITCH_BUSY",
        "SERVICE_UNAVAILABLE",
        "TEMPORARY_SYSTEM_ERROR",
        "ACQUIRER_TIMEOUT",
        "CONCURRENT_TXN_LIMIT",
    }

    KNOWN_NON_RETRYABLE_CODES: Set[str] = {
        "CARD_EXPIRED",
        "EXPIRED_CARD",
        "INVALID_VPA",
        "VPA_NOT_FOUND",
        "ACCOUNT_BLOCKED",
        "ACCOUNT_CLOSED",
        "BENEFICIARY_DOES_NOT_EXIST",
        "INVALID_ACCOUNT",
        "CARD_BLOCKED",
        "AUTHENTICATION_FAILED",
        "DO_NOT_HONOR",
        "FRAUD_SUSPECTED",
        "VELOCITY_CHECK_FAILED",
        "SANCTION_VIOLATION",
        "RISK_REJECTED",
    }

    KNOWN_CUSTOMER_ACTION_CODES: Set[str] = {
        "INSUFFICIENT_FUNDS",
        "LOW_BALANCE",
        "LIMIT_EXCEEDED",
        "DAILY_LIMIT_EXCEEDED",
        "TRANSACTION_AMOUNT_EXCEEDS_LIMIT",
        "USER_DROPPED_OFF",
        "OTP_EXPIRED",
        "OTP_INCORRECT",
        "MPIN_INCORRECT",
        "USER_ABORTED",
        "ACTION_REQUIRED",
    }

    @classmethod
    def classify(
        cls,
        error_code: str,
        failure_category: FailureCategory,
    ) -> RetryabilityClass:
        """
        Deterministically classify a failure code and category into RetryabilityClass.
        If unrecognized, strictly returns UNKNOWN (does not guess).
        """
        normalized_code = (error_code or "").strip().upper()

        # Check explicit exact code matches first
        if normalized_code in cls.KNOWN_RETRYABLE_CODES:
            return RetryabilityClass.RETRYABLE

        if normalized_code in cls.KNOWN_NON_RETRYABLE_CODES:
            return RetryabilityClass.NON_RETRYABLE

        if normalized_code in cls.KNOWN_CUSTOMER_ACTION_CODES:
            return RetryabilityClass.CUSTOMER_ACTION_REQUIRED

        # If error code is unrecognized, strictly refuse to guess.
        # Core safety principle: Unrecognized failure codes cannot be assumed retryable.
        logger.warning(
            "Unrecognized failure code '%s' under category '%s'. Marking as UNKNOWN.",
            error_code,
            failure_category,
        )
        return RetryabilityClass.UNKNOWN
