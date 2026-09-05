"""Tests for payment lifecycle states and transition engine."""

from decimal import Decimal
import uuid
import pytest

from app.lifecycle import (
    InvalidStateTransitionError,
    PaymentLifecycleManager,
    VALID_TRANSITIONS,
)
from app.models import (
    Payment,
    PaymentLifecycleState,
    PaymentMethod,
)


def create_dummy_payment(state: PaymentLifecycleState) -> Payment:
    return Payment(
        id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        amount_inr=Decimal("1000.00"),
        payment_method=PaymentMethod.UPI,
        status=state,
        idempotency_key=f"idemp_{uuid.uuid4().hex[:12]}",
    )


def test_allowed_lifecycle_happy_path():
    p = create_dummy_payment(PaymentLifecycleState.CREATED)

    # CREATED -> PROCESSING
    PaymentLifecycleManager.transition(p, PaymentLifecycleState.PROCESSING)
    assert p.status == PaymentLifecycleState.PROCESSING

    # PROCESSING -> SUCCESS (terminal)
    PaymentLifecycleManager.transition(p, PaymentLifecycleState.SUCCESS)
    assert p.status == PaymentLifecycleState.SUCCESS
    assert p.status.is_terminal is True


def test_allowed_recovery_lifecycle_path():
    p = create_dummy_payment(PaymentLifecycleState.CREATED)

    # CREATED -> PROCESSING -> FAILED
    PaymentLifecycleManager.transition(p, PaymentLifecycleState.PROCESSING)
    PaymentLifecycleManager.transition(p, PaymentLifecycleState.FAILED)
    assert p.status == PaymentLifecycleState.FAILED

    # FAILED -> RECOVERY_PENDING -> RECOVERING -> RECOVERED
    PaymentLifecycleManager.transition(p, PaymentLifecycleState.RECOVERY_PENDING)
    assert p.status.is_active_recovery is True

    PaymentLifecycleManager.transition(p, PaymentLifecycleState.RECOVERING)
    assert p.status.is_active_recovery is True

    PaymentLifecycleManager.transition(p, PaymentLifecycleState.RECOVERED)
    assert p.status == PaymentLifecycleState.RECOVERED
    assert p.status.is_terminal is True


def test_terminal_states_cannot_transition():
    terminal_states = [
        PaymentLifecycleState.SUCCESS,
        PaymentLifecycleState.RECOVERED,
        PaymentLifecycleState.STOPPED,
        PaymentLifecycleState.ESCALATED,
    ]

    for term_state in terminal_states:
        p = create_dummy_payment(term_state)
        assert term_state.is_terminal is True

        for target_state in PaymentLifecycleState:
            with pytest.raises(InvalidStateTransitionError):
                PaymentLifecycleManager.transition(p, target_state)


def test_illegal_jump_transitions():
    p = create_dummy_payment(PaymentLifecycleState.CREATED)

    # Cannot jump straight from CREATED to RECOVERED or RECOVERING
    with pytest.raises(InvalidStateTransitionError):
        PaymentLifecycleManager.transition(p, PaymentLifecycleState.RECOVERED)

    with pytest.raises(InvalidStateTransitionError):
        PaymentLifecycleManager.transition(p, PaymentLifecycleState.RECOVERING)


def test_escalation_flow():
    p = create_dummy_payment(PaymentLifecycleState.CREATED)

    # CREATED -> PROCESSING -> FAILED -> RECOVERY_PENDING -> RECOVERING -> ESCALATED
    PaymentLifecycleManager.transition(p, PaymentLifecycleState.PROCESSING)
    PaymentLifecycleManager.transition(p, PaymentLifecycleState.FAILED)
    PaymentLifecycleManager.transition(p, PaymentLifecycleState.RECOVERY_PENDING)
    PaymentLifecycleManager.transition(p, PaymentLifecycleState.RECOVERING)
    PaymentLifecycleManager.transition(p, PaymentLifecycleState.ESCALATED)
    assert p.status == PaymentLifecycleState.ESCALATED

    # ESCALATED -> RECOVERING is explicitly forbidden ❌
    with pytest.raises(InvalidStateTransitionError):
        PaymentLifecycleManager.transition(p, PaymentLifecycleState.RECOVERING)

    with pytest.raises(InvalidStateTransitionError):
        PaymentLifecycleManager.transition(p, PaymentLifecycleState.SUCCESS)
