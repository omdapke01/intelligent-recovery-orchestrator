"""State machine managing fine-grained RecoveryState transitions for RecoveryCase."""

from typing import Dict, Set

from app.models.enums import RecoveryState
from app.models.recovery_case import RecoveryCase


class InvalidRecoveryStateTransitionError(Exception):
    """Raised when an illegal transition is attempted in the recovery workflow."""

    def __init__(self, current_state: RecoveryState, target_state: RecoveryState, reason: str = ""):
        self.current_state = current_state
        self.target_state = target_state
        self.reason = reason
        msg = f"Invalid recovery state transition from {current_state.value} to {target_state.value}."
        if reason:
            msg += f" Reason: {reason}"
        super().__init__(msg)


VALID_RECOVERY_TRANSITIONS: Dict[RecoveryState, Set[RecoveryState]] = {
    RecoveryState.FAILED: {
        RecoveryState.CLASSIFIED,
        RecoveryState.STOPPED,
    },
    RecoveryState.CLASSIFIED: {
        RecoveryState.RECOVERY_PLANNED,
        RecoveryState.STOPPED,
        RecoveryState.ESCALATED,
    },
    RecoveryState.RECOVERY_PLANNED: {
        RecoveryState.GUARD_PENDING,
        RecoveryState.STOPPED,
    },
    RecoveryState.GUARD_PENDING: {
        RecoveryState.APPROVED,
        RecoveryState.STOPPED,
        RecoveryState.ESCALATED,
    },
    RecoveryState.APPROVED: {
        RecoveryState.EXECUTING,
        RecoveryState.STOPPED,
        RecoveryState.ESCALATED,
    },
    RecoveryState.EXECUTING: {
        RecoveryState.RECOVERY_SUCCEEDED,
        RecoveryState.RECOVERY_FAILED,
        RecoveryState.ESCALATED,
        RecoveryState.STOPPED,
    },
    RecoveryState.RECOVERY_FAILED: {
        RecoveryState.EXECUTING,
        RecoveryState.APPROVED,
        RecoveryState.CLASSIFIED,
        RecoveryState.STOPPED,
        RecoveryState.ESCALATED,
    },
    RecoveryState.RECOVERY_SUCCEEDED: set(),  # Terminal state
    RecoveryState.STOPPED: set(),             # Terminal state
    RecoveryState.ESCALATED: set(),           # Terminal state for automated recovery
}


class RecoveryStateMachine:
    """Validator and executor for RecoveryCase state transitions."""

    @staticmethod
    def can_transition(current: RecoveryState, target: RecoveryState) -> bool:
        return target in VALID_RECOVERY_TRANSITIONS.get(current, set())

    @classmethod
    def validate_transition(
        cls,
        current: RecoveryState,
        target: RecoveryState,
        reason: str = "",
    ) -> None:
        if not cls.can_transition(current, target):
            raise InvalidRecoveryStateTransitionError(current, target, reason)

    @classmethod
    def transition(
        cls,
        case: RecoveryCase,
        target_state: RecoveryState,
        reason: str = "",
    ) -> RecoveryCase:
        cls.validate_transition(case.recovery_state, target_state, reason)
        case.recovery_state = target_state
        return case
