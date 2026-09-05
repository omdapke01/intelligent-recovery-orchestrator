"""Domain enums for Payment Lifecycle, Methods, Route status, and Failure taxonomy."""

from enum import Enum


class PaymentLifecycleState(str, Enum):
    """Lifecycle states of a payment within the orchestration system."""
    CREATED = "CREATED"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RECOVERY_PENDING = "RECOVERY_PENDING"
    RECOVERING = "RECOVERING"
    RECOVERED = "RECOVERED"
    ESCALATED = "ESCALATED"
    STOPPED = "STOPPED"

    @property
    def is_terminal(self) -> bool:
        """Terminal states from which standard automated recovery cannot progress."""
        return self in (
            PaymentLifecycleState.SUCCESS,
            PaymentLifecycleState.RECOVERED,
            PaymentLifecycleState.STOPPED,
            PaymentLifecycleState.ESCALATED,
        )

    @property
    def is_active_recovery(self) -> bool:
        return self in (
            PaymentLifecycleState.RECOVERY_PENDING,
            PaymentLifecycleState.RECOVERING,
        )


class PaymentMethod(str, Enum):
    """Payment methods supported across merchant rails."""
    UPI = "UPI"
    CREDIT_CARD = "CREDIT_CARD"
    DEBIT_CARD = "DEBIT_CARD"
    NETBANKING = "NETBANKING"
    WALLET = "WALLET"


class RouteStatus(str, Enum):
    """Real-time operational health of a payment route."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class AttemptStatus(str, Enum):
    """Status of an individual payment attempt."""
    INITIATED = "INITIATED"
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class FailureCategory(str, Enum):
    """High-level taxonomy of payment failure causes."""
    TRANSIENT = "TRANSIENT"
    ROUTE_DEGRADATION = "ROUTE_DEGRADATION"
    CUSTOMER_ACTION_REQUIRED = "CUSTOMER_ACTION_REQUIRED"
    PERMANENT = "PERMANENT"
    FRAUD = "FRAUD"


class MerchantTier(str, Enum):
    """Merchant volume/service level tier."""
    ENTERPRISE = "ENTERPRISE"
    GROWTH = "GROWTH"
    STARTUP = "STARTUP"


class RecoveryStrategy(str, Enum):
    """Recovery strategy chosen by the orchestrator decision layer."""
    NONE = "NONE"
    DETERMINISTIC_RETRY_BACKOFF = "DETERMINISTIC_RETRY_BACKOFF"
    ROUTE_FAILOVER = "ROUTE_FAILOVER"
    NOTIFY_CUSTOMER_LINK = "NOTIFY_CUSTOMER_LINK"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    TERMINAL_ABANDON = "TERMINAL_ABANDON"


class RetryabilityClass(str, Enum):
    """Classification of payment failure retryability."""
    RETRYABLE = "RETRYABLE"
    NON_RETRYABLE = "NON_RETRYABLE"
    CUSTOMER_ACTION_REQUIRED = "CUSTOMER_ACTION_REQUIRED"
    UNKNOWN = "UNKNOWN"


class RecoveryState(str, Enum):
    """Fine-grained recovery orchestration workflow states for RecoveryCase."""
    FAILED = "FAILED"
    CLASSIFIED = "CLASSIFIED"
    RECOVERY_PLANNED = "RECOVERY_PLANNED"
    GUARD_PENDING = "GUARD_PENDING"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    RECOVERY_SUCCEEDED = "RECOVERY_SUCCEEDED"
    RECOVERY_FAILED = "RECOVERY_FAILED"
    ESCALATED = "ESCALATED"
    STOPPED = "STOPPED"

    @property
    def is_terminal(self) -> bool:
        return self in (
            RecoveryState.RECOVERY_SUCCEEDED,
            RecoveryState.STOPPED,
            RecoveryState.ESCALATED,
        )
