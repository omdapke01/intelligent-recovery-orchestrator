"""Tests for Phase 8: Horizontally Scaled AI Serving Architecture, L7 Load Balancing,

Circuit Breaking, Evaluation Benchmarking, and Interactive Dashboard Generation.
"""

import asyncio
from decimal import Decimal
import json
import os
import tempfile
import time
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.investigator import RecoveryInvestigationAgent
from app.ai.hierarchy import HierarchicalRecoveryDecisionEngine
from app.ai.instances import (
    CircuitBreaker,
    CircuitBreakerState,
    InstanceHealthState,
    ModelServiceInstance,
)
from app.ai.load_balancer import L7ModelLoadBalancer, LoadBalancingAlgorithm
from app.ai.providers.base import ModelProvider, ModelUnavailableError
from app.ai.providers.mock_provider import MockAIMode, MockAIModelProvider
from app.ai.router import ModelRouter, TaskComplexity
from app.dashboard.generator import DashboardGenerator
from app.events.broker import InMemoryEventBroker
from app.evaluation.benchmark import NaiveSingleRailBaseline, RecoveryBenchmarkRunner
from app.evaluation.models import (
    BenchmarkCase,
    BenchmarkComparisonReport,
    PricingConfig,
)
from app.models.enums import FailureCategory, PaymentMethod, RecoveryStrategy, RouteStatus
from app.orchestrator.models import PaymentRecoveryContext
from app.orchestrator.orchestrator import IntelligentRecoveryOrchestrator


# -----------------------------------------------------------------------------
# 1. L7 Load Balancer: Round Robin & Least Connections
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_l7_load_balancer_round_robin():
    """Verify L7 Load Balancer round-robin distribution across instances in a tier pool."""
    lb = L7ModelLoadBalancer(algorithm=LoadBalancingAlgorithm.ROUND_ROBIN)

    inst1 = ModelServiceInstance("inst_fast_1", "FAST_CLASSIFICATION", MockAIModelProvider())
    inst2 = ModelServiceInstance("inst_fast_2", "FAST_CLASSIFICATION", MockAIModelProvider())
    inst3 = ModelServiceInstance("inst_fast_3", "FAST_CLASSIFICATION", MockAIModelProvider())

    lb.register_instances([inst1, inst2, inst3])

    # Sequential selections should cycle 1 -> 2 -> 3 -> 1
    selected_1 = await lb.select_instance("FAST_CLASSIFICATION")
    selected_2 = await lb.select_instance("FAST_CLASSIFICATION")
    selected_3 = await lb.select_instance("FAST_CLASSIFICATION")
    selected_4 = await lb.select_instance("FAST_CLASSIFICATION")

    assert selected_1.instance_id == "inst_fast_1"
    assert selected_2.instance_id == "inst_fast_2"
    assert selected_3.instance_id == "inst_fast_3"
    assert selected_4.instance_id == "inst_fast_1"


@pytest.mark.asyncio
async def test_l7_load_balancer_least_connections():
    """Verify L7 Load Balancer selects instance with minimum active requests."""
    lb = L7ModelLoadBalancer(algorithm=LoadBalancingAlgorithm.LEAST_CONNECTIONS)

    inst_busy = ModelServiceInstance("inst_busy", "FAST_CLASSIFICATION", MockAIModelProvider())
    inst_busy.active_requests = 5

    inst_medium = ModelServiceInstance("inst_medium", "FAST_CLASSIFICATION", MockAIModelProvider())
    inst_medium.active_requests = 2

    inst_free = ModelServiceInstance("inst_free", "FAST_CLASSIFICATION", MockAIModelProvider())
    inst_free.active_requests = 0

    lb.register_instances([inst_busy, inst_medium, inst_free])

    selected = await lb.select_instance("FAST_CLASSIFICATION")
    assert selected.instance_id == "inst_free"

    # Now make inst_free busy
    inst_free.active_requests = 4
    selected_next = await lb.select_instance("FAST_CLASSIFICATION")
    assert selected_next.instance_id == "inst_medium"


# -----------------------------------------------------------------------------
# 2. Circuit Breaker: Tripping, Failover, and Half-Open Recovery
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_circuit_breaker_trip_and_failover():
    """Verify circuit breaker trips to OPEN on consecutive errors and load balancer fails over to peer."""
    lb = L7ModelLoadBalancer(algorithm=LoadBalancingAlgorithm.LEAST_CONNECTIONS)

    failing_provider = MockAIModelProvider(mode=MockAIMode.UNAVAILABLE)
    healthy_provider = MockAIModelProvider(mode=MockAIMode.VALID_RETRY)

    inst_failing = ModelServiceInstance(
        "inst_failing", "FAST_CLASSIFICATION", failing_provider, failure_threshold=2, cooldown_sec=1.0
    )
    inst_healthy = ModelServiceInstance(
        "inst_healthy", "FAST_CLASSIFICATION", healthy_provider, failure_threshold=2, cooldown_sec=1.0
    )

    lb.register_instances([inst_failing, inst_healthy])

    # Dispatch request: inst_failing will fail, breaker records failure, fails over to inst_healthy
    raw_res, used_name = await lb.dispatch(
        tier="FAST_CLASSIFICATION",
        prompt="test prompt",
        system_prompt="sys prompt",
        context={"error_code": "GATEWAY_TIMEOUT"},
    )

    assert "inst_healthy" in used_name
    assert inst_failing.circuit_breaker.consecutive_failures >= 1


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_recovery():
    """Verify circuit breaker transitions from OPEN -> HALF_OPEN -> CLOSED on successful canary probe."""
    cb = CircuitBreaker(failure_threshold=2, cooldown_sec=0.1)

    # Force 2 failures to trip
    await cb.record_failure(Exception("err 1"))
    await cb.record_failure(Exception("err 2"))

    assert cb.state == CircuitBreakerState.OPEN
    assert not await cb.can_execute()

    # Wait for cooldown
    await asyncio.sleep(0.15)

    # Cooldown elapsed -> enters HALF_OPEN
    assert await cb.can_execute()
    assert cb.state == CircuitBreakerState.HALF_OPEN

    # Canary succeeds -> resets to CLOSED
    await cb.record_success()
    assert cb.state == CircuitBreakerState.CLOSED
    assert cb.consecutive_failures == 0


# -----------------------------------------------------------------------------
# 3. Model Router: 3-Tier Task Dispatch
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_router_three_tier_dispatch():
    """Verify ModelRouter dispatches between FAST_CLASSIFICATION, DEEP_REASONING, and STRUCTURED_EXTRACTION."""
    lb = L7ModelLoadBalancer()

    fast_inst = ModelServiceInstance("inst_fast", "FAST_CLASSIFICATION", MockAIModelProvider())
    deep_inst = ModelServiceInstance("inst_deep", "DEEP_REASONING", MockAIModelProvider())
    struct_inst = ModelServiceInstance("inst_struct", "STRUCTURED_EXTRACTION", MockAIModelProvider())

    lb.register_instances([fast_inst, deep_inst, struct_inst])
    router = ModelRouter(load_balancer=lb)

    # 1. Standard transient context -> FAST_CLASSIFICATION
    ctx_fast = PaymentRecoveryContext(
        payment_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        amount_inr=Decimal("1200.00"),
        payment_method=PaymentMethod.UPI,
        route_id="ROUTE_UPI",
        route_health_score=0.95,
        route_is_active=True,
        route_status=RouteStatus.HEALTHY,
        failure_category=FailureCategory.TRANSIENT,
        error_code="GATEWAY_TIMEOUT",
        reason="Timeout",
        attempt_number=1,
        failure_created_at=time.time(),
        merchant_tier="GROWTH",
        merchant_recovery_enabled=True,
        merchant_max_auto_retries=2,
        merchant_min_recovery_amount_inr=Decimal("50.00"),
        merchant_auto_escalate_threshold_inr=Decimal("50000.00"),
    )
    comp_fast = router.assess_complexity(ctx_fast)
    assert comp_fast == TaskComplexity.FAST_CLASSIFICATION

    # 2. High-value context -> DEEP_REASONING
    ctx_deep = PaymentRecoveryContext(
        payment_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        amount_inr=Decimal("75000.00"),
        payment_method=PaymentMethod.UPI,
        route_id="ROUTE_UPI",
        route_health_score=0.95,
        route_is_active=True,
        route_status=RouteStatus.HEALTHY,
        failure_category=FailureCategory.TRANSIENT,
        error_code="GATEWAY_TIMEOUT",
        reason="Timeout",
        attempt_number=1,
        failure_created_at=time.time(),
        merchant_tier="GROWTH",
        merchant_recovery_enabled=True,
        merchant_max_auto_retries=2,
        merchant_min_recovery_amount_inr=Decimal("50.00"),
        merchant_auto_escalate_threshold_inr=Decimal("50000.00"),
    )
    comp_deep = router.assess_complexity(ctx_deep)
    assert comp_deep == TaskComplexity.DEEP_REASONING

    # 3. Extraction task -> STRUCTURED_EXTRACTION
    comp_struct = router.assess_complexity(ctx_fast, task_type="extraction")
    assert comp_struct == TaskComplexity.STRUCTURED_EXTRACTION

    # Test dispatching through load balancer
    raw_res, used_name = await router.route_and_generate(
        prompt="classify",
        system_prompt="sys",
        context={},
        complexity=TaskComplexity.FAST_CLASSIFICATION,
    )
    assert "inst_fast" in used_name


# -----------------------------------------------------------------------------
# 4. Evaluation Benchmark: Mathematical Correctness & Zero Unsafe Actions
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_baseline_vs_iro_metrics_are_correct(db_session: AsyncSession):
    """Verify evaluation benchmark arithmetic correctness without hardcoding outcome bias.

    Invariant: IRO unsafe actions must strictly equal zero.
    """
    broker = InMemoryEventBroker()
    await broker.start()

    agent = RecoveryInvestigationAgent(event_broker=broker)
    decision_engine = HierarchicalRecoveryDecisionEngine(agent=agent)
    orchestrator = IntelligentRecoveryOrchestrator(broker, decision_engine=decision_engine)

    runner = RecoveryBenchmarkRunner()
    cases = runner.generate_controlled_dataset(size=25, seed=123)

    report = await runner.run_benchmark(cases=cases, session=db_session, orchestrator=orchestrator)

    # 1. Structural validity
    assert report.total_cases == 25
    assert report.baseline_metrics.total_cases == 25
    assert report.iro_metrics.total_cases == 25

    # 2. Mathematical partition
    assert (
        report.baseline_metrics.total_cases
        == report.baseline_metrics.retryable_cases + report.baseline_metrics.non_retryable_cases
    )

    # 3. Rates bounds (must never exceed 100.0%)
    assert 0.0 <= report.baseline_metrics.recovery_rate_pct <= 100.0
    assert 0.0 <= report.iro_metrics.recovery_rate_pct <= 100.0
    assert 0.0 <= report.baseline_metrics.retryable_recovery_rate_pct <= 100.0
    assert 0.0 <= report.iro_metrics.retryable_recovery_rate_pct <= 100.0
    assert report.pricing_config.customer_notification_conversion_rate == 0.35

    # 4. Strict Safety Invariant: IRO executes zero unsafe actions
    assert report.iro_metrics.unsafe_actions_count == 0

    # 5. Arithmetic Lift Formulas
    expected_rate_lift = round(report.iro_metrics.recovery_rate_pct - report.baseline_metrics.recovery_rate_pct, 2)
    assert report.absolute_recovery_rate_lift_pct == expected_rate_lift

    expected_rev_lift = round(report.iro_metrics.recovered_revenue_inr - report.baseline_metrics.recovered_revenue_inr, 2)
    assert report.incremental_recovered_revenue_inr == expected_rev_lift


# -----------------------------------------------------------------------------
# 5. ROI Multiplier Arithmetic
# -----------------------------------------------------------------------------

def test_financial_roi_calculation():
    """Verify ROI formula consistency: ROI = incremental_revenue / ai_cost."""
    # Test case A: Positive incremental revenue and positive cost
    inc_rev = 15000.0
    ai_cost = 10.0
    roi = inc_rev / ai_cost
    assert roi == 1500.0

    # Test case B: Zero AI cost safely defaults to 0.0 without division by zero
    ai_cost_zero = 0.0
    roi_safe = (inc_rev / ai_cost_zero) if ai_cost_zero > 0 else 0.0
    assert roi_safe == 0.0


# -----------------------------------------------------------------------------
# 6. Segmented Metrics Consistency
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_segmented_metrics_consistency(db_session: AsyncSession):
    """Verify segmented metrics partition cleanly across payment methods and value segments."""
    broker = InMemoryEventBroker()
    await broker.start()
    orchestrator = IntelligentRecoveryOrchestrator(broker)

    runner = RecoveryBenchmarkRunner()
    cases = runner.generate_controlled_dataset(size=20, seed=42)

    report = await runner.run_benchmark(cases=cases, session=db_session, orchestrator=orchestrator)

    # Sum of cases in payment methods segmentation equals total cases
    sum_method_cases = sum(sm.total_cases for sm in report.by_payment_method.values())
    assert sum_method_cases == report.total_cases

    # Sum of cases in value segmentation equals total cases
    sum_value_cases = sum(sv.total_cases for sv in report.by_value_segment.values())
    assert sum_value_cases == report.total_cases


# -----------------------------------------------------------------------------
# 7. Dashboard HTML, JSON, and Terminal Output
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_html_and_json_generation(db_session: AsyncSession):
    """Verify DashboardGenerator exports valid HTML, JSON, and terminal summary."""
    broker = InMemoryEventBroker()
    await broker.start()
    orchestrator = IntelligentRecoveryOrchestrator(broker)

    runner = RecoveryBenchmarkRunner()
    cases = runner.generate_controlled_dataset(size=10, seed=99)
    report = await runner.run_benchmark(cases=cases, session=db_session, orchestrator=orchestrator)

    with tempfile.TemporaryDirectory() as tmpdir:
        html_path = os.path.join(tmpdir, "test_dashboard.html")
        json_path = os.path.join(tmpdir, "test_report.json")

        # 1. HTML generation
        generated_html = DashboardGenerator.generate_html(report, filepath=html_path)
        assert os.path.exists(generated_html)
        with open(generated_html, "r", encoding="utf-8") as f:
            html_text = f.read()
            assert "Intelligent Recovery Orchestrator" in html_text
            assert "Synthetic Simulation Assumptions" in html_text
            assert "Unsafe Actions Blocked" in html_text

        # 2. JSON generation
        generated_json = DashboardGenerator.generate_json(report, filepath=json_path)
        assert os.path.exists(generated_json)
        with open(generated_json, "r", encoding="utf-8") as f:
            parsed = json.load(f)
            assert "baseline_metrics" in parsed
            assert "iro_metrics" in parsed
            assert "comparison" in parsed
            assert "pricing_assumptions" in parsed

        # 3. Terminal output
        terminal_summary = DashboardGenerator.render_terminal_summary(report)
        assert "RAZORPAY INTELLIGENT RECOVERY ORCHESTRATOR" in terminal_summary
        assert "INR" in terminal_summary


# -----------------------------------------------------------------------------
# 8. L7 Load Balancer Pipeline Integration
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_l7_load_balancer_dispatch_in_pipeline(db_session: AsyncSession):
    """Verify that when ModelRouter with L7 Load Balancer is wired into the decision pipeline,
    AI recommendations genuinely dispatch to the load-balanced cluster and track telemetry.
    """
    from app.ai.gateway import AIModelGateway

    broker = InMemoryEventBroker()
    await broker.start()

    lb = L7ModelLoadBalancer(algorithm=LoadBalancingAlgorithm.LEAST_CONNECTIONS)
    fast_inst = ModelServiceInstance("test_fast", "FAST_CLASSIFICATION", MockAIModelProvider())
    deep_inst = ModelServiceInstance("test_deep", "DEEP_REASONING", MockAIModelProvider(mode=MockAIMode.VALID_ALTERNATE_METHOD))
    lb.register_instances([fast_inst, deep_inst])

    router = ModelRouter(load_balancer=lb)
    gateway = AIModelGateway(router=router)
    agent = RecoveryInvestigationAgent(event_broker=broker, ai_gateway=gateway)
    decision_engine = HierarchicalRecoveryDecisionEngine(ai_gateway=gateway, agent=agent)
    orchestrator = IntelligentRecoveryOrchestrator(broker=broker, decision_engine=decision_engine)

    runner = RecoveryBenchmarkRunner()
    cases = runner.generate_controlled_dataset(size=10, seed=42)
    report = await runner.run_benchmark(cases=cases, session=db_session, orchestrator=orchestrator)

    # Dispatches through load balancer must be non-zero
    assert router.routing_stats["load_balancer_dispatches"] > 0
    status = lb.get_cluster_status()
    assert status["total_instances"] == 2

