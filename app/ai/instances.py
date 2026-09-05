"""Model Service Instances and Circuit Breaker for horizontally scaled AI serving."""

import asyncio
from datetime import datetime, timezone
from enum import Enum
import logging
import time
from typing import Any, Dict, List, Optional
import uuid

from app.ai.providers.base import ModelProvider, ModelProviderError, ModelTimeoutError, ModelUnavailableError

logger = logging.getLogger("iro.ai.instances")


class InstanceHealthState(str, Enum):
    """Health status of an individual model service instance."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


class CircuitBreakerState(str, Enum):
    """Circuit breaker operational state."""
    CLOSED = "CLOSED"        # Normal operations: requests flow freely
    OPEN = "OPEN"            # Tripped: requests fail fast without calling provider
    HALF_OPEN = "HALF_OPEN"  # Canary probing: single test request permitted


class CircuitBreaker:
    """Per-instance circuit breaker protecting downstream models from cascading outages."""

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_sec: float = 5.0,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_sec = cooldown_sec
        self.state = CircuitBreakerState.CLOSED
        self.consecutive_failures = 0
        self.last_failure_time: Optional[float] = None
        self.successful_probes = 0
        self._lock = asyncio.Lock()

    async def can_execute(self) -> bool:
        """Check whether a request is permitted under current breaker state."""
        async with self._lock:
            now = time.monotonic()
            if self.state == CircuitBreakerState.CLOSED:
                return True

            if self.state == CircuitBreakerState.OPEN:
                # Check if cooldown has elapsed to allow a canary probe
                if self.last_failure_time and (now - self.last_failure_time >= self.cooldown_sec):
                    logger.info("[CIRCUIT BREAKER HALF_OPEN] Cooldown elapsed; entering HALF_OPEN for canary probe.")
                    self.state = CircuitBreakerState.HALF_OPEN
                    return True
                return False

            if self.state == CircuitBreakerState.HALF_OPEN:
                # Allow canary request
                return True

            return False

    async def record_success(self) -> None:
        """Record a successful execution, resetting failure counters."""
        async with self._lock:
            if self.state == CircuitBreakerState.HALF_OPEN:
                logger.info("[CIRCUIT BREAKER RECOVERED] Canary request succeeded; resetting to CLOSED.")
            self.state = CircuitBreakerState.CLOSED
            self.consecutive_failures = 0
            self.successful_probes += 1

    async def record_failure(self, error: Exception) -> None:
        """Record an execution failure, potentially tripping the circuit breaker."""
        async with self._lock:
            self.consecutive_failures += 1
            self.last_failure_time = time.monotonic()

            if self.state == CircuitBreakerState.HALF_OPEN:
                logger.warning(f"[CIRCUIT BREAKER RE-TRIPPED] Canary request failed ({error}); returning to OPEN.")
                self.state = CircuitBreakerState.OPEN
            elif self.consecutive_failures >= self.failure_threshold:
                logger.warning(
                    f"[CIRCUIT BREAKER TRIPPED] Instance reached {self.consecutive_failures} consecutive failures. "
                    f"Tripping to OPEN for {self.cooldown_sec}s cooldown."
                )
                self.state = CircuitBreakerState.OPEN


class ModelServiceInstance:
    """Horizontally scaled model service worker instance.

    Tracks active concurrency, telemetry, token accounting, and circuit breaking.
    """

    # Synthetic Benchmark Pricing Assumptions (USD per 1,000 tokens)
    SYNTHETIC_PRICING_USD_PER_1K = {
        "FAST_CLASSIFICATION": {"input": 0.0005, "output": 0.0015},
        "DEEP_REASONING": {"input": 0.0100, "output": 0.0300},
        "STRUCTURED_EXTRACTION": {"input": 0.0020, "output": 0.0060},
    }
    SYNTHETIC_USD_TO_INR_RATE = 85.0

    def __init__(
        self,
        instance_id: str,
        tier: str,
        provider: ModelProvider,
        failure_threshold: int = 3,
        cooldown_sec: float = 5.0,
        simulated_failure_rate: float = 0.0,
    ):
        self.instance_id = instance_id
        self.tier = tier
        self.provider = provider
        self.circuit_breaker = CircuitBreaker(failure_threshold=failure_threshold, cooldown_sec=cooldown_sec)
        self.health_state = InstanceHealthState.HEALTHY
        self.simulated_failure_rate = simulated_failure_rate

        # Concurrency & Telemetry
        self.active_requests: int = 0
        self.total_requests: int = 0
        self.successful_requests: int = 0
        self.failed_requests: int = 0
        self.total_latency_ms: float = 0.0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self._metrics_lock = asyncio.Lock()

    @property
    def is_available(self) -> bool:
        """Returns True if instance is healthy/degraded and circuit breaker is not OPEN."""
        return (
            self.health_state != InstanceHealthState.UNHEALTHY
            and self.circuit_breaker.state != CircuitBreakerState.OPEN
        )

    @property
    def avg_latency_ms(self) -> float:
        """Average latency in milliseconds."""
        if self.successful_requests == 0:
            return 0.0
        return self.total_latency_ms / self.successful_requests

    @property
    def synthetic_inference_cost_usd(self) -> float:
        """Calculate synthetic inference cost in USD based on benchmark pricing."""
        rates = self.SYNTHETIC_PRICING_USD_PER_1K.get(
            self.tier, {"input": 0.001, "output": 0.002}
        )
        input_cost = (self.prompt_tokens / 1000.0) * rates["input"]
        output_cost = (self.completion_tokens / 1000.0) * rates["output"]
        return input_cost + output_cost

    @property
    def synthetic_inference_cost_inr(self) -> float:
        """Calculate synthetic inference cost in INR based on benchmark pricing."""
        return self.synthetic_inference_cost_usd * self.SYNTHETIC_USD_TO_INR_RATE

    async def generate_recommendation(
        self,
        prompt: str,
        system_prompt: str,
        context: Dict[str, Any],
    ) -> str:
        """Invoke underlying model provider with concurrency tracking and circuit breaker."""
        can_run = await self.circuit_breaker.can_execute()
        if not can_run:
            raise ModelUnavailableError(
                f"Instance '{self.instance_id}' circuit breaker is OPEN. Fast-failing request."
            )

        # Increment in-flight active concurrency
        async with self._metrics_lock:
            self.active_requests += 1
            self.total_requests += 1

        t0 = time.perf_counter()
        try:
            # Check for simulated chaos failure
            if self.simulated_failure_rate > 0:
                import random
                if random.random() < self.simulated_failure_rate:
                    raise ModelUnavailableError(f"Simulated fault injection on instance {self.instance_id}")

            raw_response = await self.provider.generate_recommendation(
                prompt=prompt,
                system_prompt=system_prompt,
                context=context,
            )

            latency_ms = (time.perf_counter() - t0) * 1000.0

            # Token accounting simulation
            # Fast: ~150 prompt, ~50 completion
            # Deep: ~450 prompt, ~200 completion
            # Structured: ~250 prompt, ~100 completion
            prompt_est = 450 if self.tier == "DEEP_REASONING" else (250 if self.tier == "STRUCTURED_EXTRACTION" else 150)
            comp_est = 200 if self.tier == "DEEP_REASONING" else (100 if self.tier == "STRUCTURED_EXTRACTION" else 50)

            async with self._metrics_lock:
                self.successful_requests += 1
                self.total_latency_ms += latency_ms
                self.prompt_tokens += prompt_est
                self.completion_tokens += comp_est

            await self.circuit_breaker.record_success()

            # Dynamic health check based on latency
            if latency_ms > 2000.0:
                self.health_state = InstanceHealthState.DEGRADED
            else:
                self.health_state = InstanceHealthState.HEALTHY

            return raw_response

        except Exception as err:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            async with self._metrics_lock:
                self.failed_requests += 1
                self.total_latency_ms += latency_ms

            await self.circuit_breaker.record_failure(err)

            if self.circuit_breaker.state == CircuitBreakerState.OPEN:
                self.health_state = InstanceHealthState.UNHEALTHY
            else:
                self.health_state = InstanceHealthState.DEGRADED

            raise
        finally:
            async with self._metrics_lock:
                self.active_requests = max(0, self.active_requests - 1)

    def get_telemetry(self) -> Dict[str, Any]:
        """Return a snapshot of instance telemetry."""
        return {
            "instance_id": self.instance_id,
            "tier": self.tier,
            "health_state": self.health_state.value,
            "circuit_breaker_state": self.circuit_breaker.state.value,
            "active_requests": self.active_requests,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "synthetic_cost_usd": round(self.synthetic_inference_cost_usd, 6),
            "synthetic_cost_inr": round(self.synthetic_inference_cost_inr, 4),
        }
