"""Payment Lifecycle State Machine and transition validator."""

from typing import Dict, Set
from app.models.enums import PaymentLifecycleState
from app.models.payment import Payment


class InvalidStateTransitionError(Exception):
    """Raised when an illegal lifecycle state transition is attempted."""
    def __init__(self, current_state: PaymentLifecycleState, target_state: PaymentLifecycleState, reason: str = ""):
        self.current_state = current_state
        self.target_state = target_state
        self.reason = reason
        msg = f"Invalid state transition from {current_state.value} to {target_state.value}."
        if reason:
            msg += f" Reason: {reason}"
        super().__init__(msg)


# Strict map of allowed transitions
VALID_TRANSITIONS: Dict[PaymentLifecycleState, Set[PaymentLifecycleState]] = {
    PaymentLifecycleState.CREATED: {
        PaymentLifecycleState.PROCESSING,
        PaymentLifecycleState.STOPPED,
    },
    PaymentLifecycleState.PROCESSING: {
        PaymentLifecycleState.SUCCESS,
        PaymentLifecycleState.FAILED,
    },
    PaymentLifecycleState.SUCCESS: set(),  # Terminal state
    PaymentLifecycleState.FAILED: {
        PaymentLifecycleState.RECOVERY_PENDING,
        PaymentLifecycleState.STOPPED,
    },
    PaymentLifecycleState.RECOVERY_PENDING: {
        PaymentLifecycleState.RECOVERING,
        PaymentLifecycleState.STOPPED,
    },
    PaymentLifecycleState.RECOVERING: {
        PaymentLifecycleState.RECOVERED,
        PaymentLifecycleState.ESCALATED,
        PaymentLifecycleState.STOPPED,
        PaymentLifecycleState.RECOVERY_PENDING,
    },
    PaymentLifecycleState.RECOVERED: set(),  # Terminal state
    PaymentLifecycleState.ESCALATED: set(),  # Terminal for automated recovery (ESCALATED -> RECOVERING ❌)
    PaymentLifecycleState.STOPPED: set(),    # Terminal state
}


class PaymentLifecycleManager:
    """Manages lifecycle transitions for Payment entities."""

    @staticmethod
    def can_transition(current: PaymentLifecycleState, target: PaymentLifecycleState) -> bool:
        """Check whether transition from current to target is allowed."""
        return target in VALID_TRANSITIONS.get(current, set())

    @classmethod
    def validate_transition(
        cls,
        current: PaymentLifecycleState,
        target: PaymentLifecycleState,
        reason: str = "",
    ) -> None:
        """Validate transition, raising InvalidStateTransitionError if illegal."""
        if not cls.can_transition(current, target):
            raise InvalidStateTransitionError(current, target, reason)

    @classmethod
    def transition(
        cls,
        payment: Payment,
        target_state: PaymentLifecycleState,
        reason: str = "",
    ) -> Payment:
        """Execute a validated lifecycle state transition on a payment."""
        cls.validate_transition(payment.status, target_state, reason)
        payment.status = target_state
        return payment
