"""Layer 7 Model Load Balancer managing instance pools, health-aware traffic distribution,

and circuit breaker failover.

Strict Invariant: The L7 load balancer is purely an infrastructure-level traffic router
and resilience manager. It is NOT the business decision maker.
"""

import asyncio
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.ai.instances import InstanceHealthState, ModelServiceInstance
from app.ai.providers.base import ModelProviderError, ModelUnavailableError

logger = logging.getLogger("iro.ai.load_balancer")


class LoadBalancingAlgorithm(str, Enum):
    """Supported L7 load balancing algorithms."""
    ROUND_ROBIN = "ROUND_ROBIN"
    LEAST_CONNECTIONS = "LEAST_CONNECTIONS"


class L7ModelLoadBalancer:
    """L7 Load Balancer distributing requests across horizontally scaled model service instances."""

    def __init__(
        self,
        algorithm: LoadBalancingAlgorithm = LoadBalancingAlgorithm.LEAST_CONNECTIONS,
    ):
        self.algorithm = algorithm
        # Map: tier_name -> List[ModelServiceInstance]
        self.pools: Dict[str, List[ModelServiceInstance]] = {
            "FAST_CLASSIFICATION": [],
            "DEEP_REASONING": [],
            "STRUCTURED_EXTRACTION": [],
        }
        self._pool_rr_indices: Dict[str, int] = {
            "FAST_CLASSIFICATION": 0,
            "DEEP_REASONING": 0,
            "STRUCTURED_EXTRACTION": 0,
        }
        self._lb_lock = asyncio.Lock()

    def register_instance(self, instance: ModelServiceInstance) -> None:
        """Register a model service instance into its designated tier pool."""
        tier = instance.tier
        if tier not in self.pools:
            self.pools[tier] = []
            self._pool_rr_indices[tier] = 0
        self.pools[tier].append(instance)
        logger.info(
            f"[L7 LOAD BALANCER] Registered instance '{instance.instance_id}' into pool '{tier}'. "
            f"Pool size now: {len(self.pools[tier])}"
        )

    def register_instances(self, instances: List[ModelServiceInstance]) -> None:
        """Register multiple model service instances."""
        for inst in instances:
            self.register_instance(inst)

    async def select_instance(self, tier: str) -> ModelServiceInstance:
        """Select an optimal available instance from the pool according to algorithm."""
        pool = self.pools.get(tier, [])
        if not pool:
            raise ModelUnavailableError(f"No instances registered in L7 pool for tier '{tier}'.")

        # Filter available instances (healthy/degraded, breaker not open)
        available = [inst for inst in pool if inst.is_available]
        if not available:
            # If all are tripped, check if any instance is degraded or can accept canary
            logger.warning(
                f"[L7 LOAD BALANCER CRITICAL] All {len(pool)} instances in pool '{tier}' are unavailable. "
                f"Checking for any instance eligible for canary probe."
            )
            for inst in pool:
                if await inst.circuit_breaker.can_execute():
                    return inst
            raise ModelUnavailableError(
                f"All instances in pool '{tier}' are down or circuit-broken."
            )

        async with self._lb_lock:
            if self.algorithm == LoadBalancingAlgorithm.ROUND_ROBIN:
                idx = self._pool_rr_indices.get(tier, 0)
                selected = available[idx % len(available)]
                self._pool_rr_indices[tier] = (idx + 1) % len(available)
                return selected

            elif self.algorithm == LoadBalancingAlgorithm.LEAST_CONNECTIONS:
                # Select available instance with minimum active requests
                selected = min(available, key=lambda inst: inst.active_requests)
                return selected

            else:
                return available[0]

    async def dispatch(
        self,
        tier: str,
        prompt: str,
        system_prompt: str,
        context: Dict[str, Any],
        max_retries_within_pool: int = 2,
    ) -> Tuple[str, str]:
        """Dispatch a request to the appropriate model tier pool with automatic failover.

        Returns:
            Tuple[raw_response_text, instance_id_used]
        """
        pool = self.pools.get(tier, [])
        if not pool:
            raise ModelUnavailableError(f"No instances configured for tier '{tier}'.")

        attempts = 0
        last_error: Optional[Exception] = None
        tried_instances = set()

        while attempts <= max_retries_within_pool:
            attempts += 1
            try:
                instance = await self.select_instance(tier)
                if instance.instance_id in tried_instances and len(tried_instances) < len(pool):
                    # Try to find an untried instance if available
                    untried = [i for i in pool if i.instance_id not in tried_instances and i.is_available]
                    if untried:
                        instance = untried[0]

                tried_instances.add(instance.instance_id)

                logger.info(
                    f"[L7 LOAD BALANCER] Routing task ({tier}) to instance '{instance.instance_id}' "
                    f"(active_reqs={instance.active_requests}, breaker={instance.circuit_breaker.state.value})"
                )

                raw_response = await instance.generate_recommendation(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    context=context,
                )
                return raw_response, f"{instance.instance_id}:{tier}"

            except Exception as err:
                last_error = err
                logger.warning(
                    f"[L7 INSTANCE FAILOVER] Instance dispatch failed ({err}). "
                    f"Attempt {attempts}/{max_retries_within_pool + 1}. Attempting peer failover..."
                )

        logger.error(
            f"[L7 POOL EXHAUSTION] All retry attempts across pool '{tier}' failed. "
            f"Last error: {last_error}"
        )
        raise ModelUnavailableError(
            f"L7 Load Balancer could not fulfill request in pool '{tier}': {last_error}"
        ) from last_error

    def get_cluster_status(self) -> Dict[str, Any]:
        """Generate cluster health and capacity telemetry."""
        total_instances = 0
        healthy_count = 0
        degraded_count = 0
        unhealthy_count = 0
        total_active_reqs = 0
        total_completed_reqs = 0
        total_prompt_tokens = 0
        total_comp_tokens = 0
        total_cost_usd = 0.0
        total_cost_inr = 0.0

        pools_summary = {}

        for tier, instances in self.pools.items():
            pool_telemetry = []
            for inst in instances:
                t = inst.get_telemetry()
                pool_telemetry.append(t)
                total_instances += 1
                if t["health_state"] == "HEALTHY":
                    healthy_count += 1
                elif t["health_state"] == "DEGRADED":
                    degraded_count += 1
                else:
                    unhealthy_count += 1

                total_active_reqs += t["active_requests"]
                total_completed_reqs += t["successful_requests"]
                total_prompt_tokens += t["prompt_tokens"]
                total_comp_tokens += t["completion_tokens"]
                total_cost_usd += t["synthetic_cost_usd"]
                total_cost_inr += t["synthetic_cost_inr"]

            pools_summary[tier] = {
                "instance_count": len(instances),
                "instances": pool_telemetry,
            }

        return {
            "algorithm": self.algorithm.value,
            "total_instances": total_instances,
            "healthy_instances": healthy_count,
            "degraded_instances": degraded_count,
            "unhealthy_instances": unhealthy_count,
            "total_active_requests": total_active_reqs,
            "total_completed_requests": total_completed_reqs,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_comp_tokens,
            "total_tokens": total_prompt_tokens + total_comp_tokens,
            "synthetic_total_cost_usd": round(total_cost_usd, 4),
            "synthetic_total_cost_inr": round(total_cost_inr, 2),
            "pools": pools_summary,
        }
