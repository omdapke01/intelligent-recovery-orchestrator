"""Mock Payment Provider sandbox supporting 5 distinct simulation outcomes.

Guarantees execution duration is strictly bounded below the Redis lock TTL.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional

from app.config import settings
from app.models.enums import PaymentLifecycleState, PaymentMethod

logger = logging.getLogger("iro.execution.provider")


class ProviderOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    UNAVAILABLE = "UNAVAILABLE"


class ProviderTimeoutException(Exception):
    """Raised when downstream payment gateway times out."""
    pass


class ProviderUnavailableException(Exception):
    """Raised when downstream payment provider is unavailable (e.g. 503)."""
    pass


class DuplicateGatewayRequestException(Exception):
    """Raised when provider gateway detects duplicate idempotency key submission."""
    pass


@dataclass
class PaymentExecutionRequest:
    payment_id: uuid.UUID
    attempt_number: int
    idempotency_key: str
    amount_inr: Decimal
    route_id: str
    payment_method: PaymentMethod


@dataclass
class PaymentExecutionResponse:
    success: bool
    status: PaymentLifecycleState
    gateway_ref_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    latency_ms: int = 50


class MockPaymentProvider:
    """Configurable payment provider sandbox for testing recovery execution.

    Supports:
    - retry success
    - retry failure
    - timeout
    - duplicate request
    - provider unavailable
    """

    def __init__(
        self,
        default_outcome: ProviderOutcome = ProviderOutcome.SUCCESS,
        timeout_sec: Optional[float] = None,
    ):
        self.default_outcome = default_outcome
        # Guardrail: provider execution timeout MUST be strictly less than Redis lock TTL (10s)
        self.timeout_sec = timeout_sec or settings.PROVIDER_TIMEOUT_SEC
        self.outcomes_by_idempotency: Dict[str, ProviderOutcome] = {}
        self.outcomes_by_payment: Dict[uuid.UUID, List[ProviderOutcome]] = {}
        self.call_history: List[PaymentExecutionRequest] = []
        self.seen_idempotency_keys: Dict[str, int] = {}
        self._lock = asyncio.Lock()

    @property
    def call_count(self) -> int:
        return len(self.call_history)

    @property
    def calls(self) -> List[PaymentExecutionRequest]:
        return self.call_history

    def set_outcome_for_idempotency_key(self, key: str, outcome: ProviderOutcome) -> None:
        """Explicitly override outcome for a specific idempotency key."""
        self.outcomes_by_idempotency[key] = outcome

    def set_sequence_for_payment(self, payment_id: uuid.UUID, outcomes: List[ProviderOutcome]) -> None:
        """Define a sequence of outcomes for successive attempts of a payment."""
        self.outcomes_by_payment[payment_id] = list(outcomes)

    async def execute_payment(self, request: PaymentExecutionRequest) -> PaymentExecutionResponse:
        """Execute payment attempt against mock gateway under timeout boundary."""
        async with self._lock:
            self.call_history.append(request)
            self.seen_idempotency_keys[request.idempotency_key] = (
                self.seen_idempotency_keys.get(request.idempotency_key, 0) + 1
            )
            call_count_for_key = self.seen_idempotency_keys[request.idempotency_key]

        # Check if caller reused an idempotency key against the gateway
        # If outcome configured is DUPLICATE_REQUEST or if gateway sees 2nd attempt with same key
        outcome = self._determine_outcome(request, call_count_for_key)

        logger.info(
            f"[MOCK PROVIDER] Executing payment_id={request.payment_id}, "
            f"attempt={request.attempt_number}, outcome={outcome.value}, "
            f"idempotency_key={request.idempotency_key}"
        )

        try:
            return await asyncio.wait_for(
                self._simulate_outcome(request, outcome),
                timeout=self.timeout_sec,
            )
        except asyncio.TimeoutError:
            logger.error(
                f"[MOCK PROVIDER TIMEOUT] Execution exceeded bounded timeout of {self.timeout_sec}s "
                f"for payment_id={request.payment_id}"
            )
            raise ProviderTimeoutException(
                f"Downstream payment gateway timed out after {self.timeout_sec}s"
            )

    def _determine_outcome(self, request: PaymentExecutionRequest, call_count_for_key: int) -> ProviderOutcome:
        if request.idempotency_key in self.outcomes_by_idempotency:
            return self.outcomes_by_idempotency[request.idempotency_key]

        if request.payment_id in self.outcomes_by_payment and self.outcomes_by_payment[request.payment_id]:
            return self.outcomes_by_payment[request.payment_id].pop(0)

        # If duplicate call on same key and default is not already failure, simulate duplicate
        if call_count_for_key > 1 and self.default_outcome == ProviderOutcome.DUPLICATE_REQUEST:
            return ProviderOutcome.DUPLICATE_REQUEST

        return self.default_outcome

    async def _simulate_outcome(
        self, request: PaymentExecutionRequest, outcome: ProviderOutcome
    ) -> PaymentExecutionResponse:
        # Simulate slight network processing
        await asyncio.sleep(0.02)

        if outcome == ProviderOutcome.SUCCESS:
            return PaymentExecutionResponse(
                success=True,
                status=PaymentLifecycleState.SUCCESS,
                gateway_ref_id=f"pay_gw_{uuid.uuid4().hex[:12]}",
                latency_ms=45,
            )

        elif outcome == ProviderOutcome.FAILURE:
            return PaymentExecutionResponse(
                success=False,
                status=PaymentLifecycleState.FAILED,
                error_code="INSUFFICIENT_FUNDS",
                error_message="Customer account has insufficient funds for debit",
                latency_ms=50,
            )

        elif outcome == ProviderOutcome.TIMEOUT:
            # Simulate hung gateway that triggers ProviderTimeoutException
            await asyncio.sleep(self.timeout_sec + 0.1)
            raise ProviderTimeoutException("Gateway did not respond")

        elif outcome == ProviderOutcome.DUPLICATE_REQUEST:
            raise DuplicateGatewayRequestException(
                f"Duplicate idempotency key '{request.idempotency_key}' detected by gateway."
            )

        elif outcome == ProviderOutcome.UNAVAILABLE:
            raise ProviderUnavailableException(
                "Acquiring bank switch returned HTTP 503 Service Unavailable."
            )

        raise ValueError(f"Unknown outcome: {outcome}")

    @property
    def total_calls(self) -> int:
        return len(self.call_history)

    def reset(self) -> None:
        self.call_history.clear()
        self.seen_idempotency_keys.clear()
        self.outcomes_by_idempotency.clear()
        self.outcomes_by_payment.clear()
