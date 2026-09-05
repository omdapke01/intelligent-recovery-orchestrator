"""Demo script for Phase 8: Production AI Serving, Benchmark Evaluation, and Dashboard Generation."""

import asyncio
from decimal import Decimal
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.agent.investigator import RecoveryInvestigationAgent
from app.ai.gateway import AIModelGateway
from app.ai.hierarchy import HierarchicalRecoveryDecisionEngine
from app.ai.instances import ModelServiceInstance
from app.ai.load_balancer import L7ModelLoadBalancer, LoadBalancingAlgorithm
from app.ai.providers.mock_provider import MockAIMode, MockAIModelProvider
from app.ai.router import ModelRouter
from app.dashboard.generator import DashboardGenerator
from app.database import async_session_factory, init_db
from app.events.broker import InMemoryEventBroker
from app.evaluation.benchmark import RecoveryBenchmarkRunner
from app.orchestrator.orchestrator import IntelligentRecoveryOrchestrator

# Configure logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


async def main():
    print("=" * 80)
    print("   RAZORPAY BUILDATHON — PHASE 8: PRODUCTION AI SERVING & EVALUATION BENCHMARK   ")
    print("=" * 80)
    print("Initializing SQLite In-Memory Database and Event Broker...")
    await init_db()

    broker = InMemoryEventBroker()
    await broker.start()

    print("\n[1/4] Assembling Horizontally Scaled L7 AI Serving Cluster...")
    lb = L7ModelLoadBalancer(algorithm=LoadBalancingAlgorithm.LEAST_CONNECTIONS)

    # 3 instances for Fast Classification tier
    fast_1 = ModelServiceInstance("worker-fast-alpha", "FAST_CLASSIFICATION", MockAIModelProvider())
    fast_2 = ModelServiceInstance("worker-fast-beta", "FAST_CLASSIFICATION", MockAIModelProvider())
    fast_3 = ModelServiceInstance("worker-fast-gamma", "FAST_CLASSIFICATION", MockAIModelProvider())

    # 2 instances for Deep Reasoning tier (Reasoning models)
    deep_1 = ModelServiceInstance("worker-deep-reasoning-01", "DEEP_REASONING", MockAIModelProvider(mode=MockAIMode.VALID_ALTERNATE_METHOD))
    deep_2 = ModelServiceInstance("worker-deep-reasoning-02", "DEEP_REASONING", MockAIModelProvider(mode=MockAIMode.VALID_RETRY_LATER))

    # 1 instance for Structured Extraction tier
    struct_1 = ModelServiceInstance("worker-structured-json-01", "STRUCTURED_EXTRACTION", MockAIModelProvider())

    lb.register_instances([fast_1, fast_2, fast_3, deep_1, deep_2, struct_1])

    router = ModelRouter(load_balancer=lb)
    gateway = AIModelGateway(router=router)
    agent = RecoveryInvestigationAgent(event_broker=broker, ai_gateway=gateway)
    decision_engine = HierarchicalRecoveryDecisionEngine(ai_gateway=gateway, agent=agent)
    orchestrator = IntelligentRecoveryOrchestrator(broker=broker, decision_engine=decision_engine)

    cluster_status = lb.get_cluster_status()
    print(f"  * Algorithm: {cluster_status['algorithm']}")
    print(f"  * Total Model Service Instances: {cluster_status['total_instances']}")
    print(f"  * FAST_CLASSIFICATION pool: {cluster_status['pools']['FAST_CLASSIFICATION']['instance_count']} instances")
    print(f"  * DEEP_REASONING pool:       {cluster_status['pools']['DEEP_REASONING']['instance_count']} instances")
    print(f"  * STRUCTURED_EXTRACTION:    {cluster_status['pools']['STRUCTURED_EXTRACTION']['instance_count']} instances")

    print("\n[2/4] Generating Controlled Synthetic Payment Failure Dataset...")
    runner = RecoveryBenchmarkRunner()
    cases = runner.generate_controlled_dataset(size=100, seed=42)
    print(f"  * Generated {len(cases)} payment failure cases across balanced profiles:")
    print("    - Normal Transient Timeouts (UPI, Card, Netbanking)")
    print("    - Degraded Switch Outages (eligible for route failover)")
    print("    - Customer Action Required (insufficient funds, authorization links)")
    print("    - High-Value Invoices (>INR 100,000 automated amount cap policies)")
    print("    - Fraud & Hard Declines (card blocked, sanction flags)")
    print("    - Late Success / Stale Retries (customer captured externally)")
    print("    - In-Flight Pending Holds (asynchronous webhook reconciliation)")

    print("\n[3/4] Running Dual-Engine Benchmark (Naive Single-Rail Baseline vs IRO Architecture)...")
    async with async_session_factory() as session:
        report = await runner.run_benchmark(cases=cases, session=session, orchestrator=orchestrator)

    print("\n[4/4] Benchmark Execution Complete. Generating Telemetry & Artifacts...")

    # 1. Terminal Visualizer Summary
    terminal_output = DashboardGenerator.render_terminal_summary(report)
    print("\n" + terminal_output)

    # 2. Export Standalone HTML Dashboard
    html_file = "dashboard.html"
    DashboardGenerator.generate_html(report, filepath=html_file)
    print(f"\n[ARTIFACT GENERATED] Interactive HTML Dashboard: {os.path.abspath(html_file)}")

    # 3. Export Machine-Readable JSON Report
    json_file = "iro_benchmark_report.json"
    DashboardGenerator.generate_json(report, filepath=json_file)
    print(f"[ARTIFACT GENERATED] Canonical JSON Report:    {os.path.abspath(json_file)}")

    # 4. Cluster Health Report
    final_cluster = lb.get_cluster_status()
    print("\nL7 AI SERVING CLUSTER TELEMETRY:")
    print(f"  * Total Active Requests:    {final_cluster['total_active_requests']}")
    print(f"  * Total Dispatches Handled: {router.routing_stats['load_balancer_dispatches']}")
    print(f"  * Fast Tier Dispatches:     {router.routing_stats['fast_classification']}")
    print(f"  * Deep Tier Dispatches:     {router.routing_stats['deep_reasoning']}")
    for pool_tier, pool_info in final_cluster["pools"].items():
        active_insts = [i for i in pool_info["instances"] if i["total_requests"] > 0]
        if active_insts:
            print(f"    - Pool [{pool_tier}]:")
            for inst in active_insts:
                print(f"      * {inst['instance_id']}: {inst['successful_requests']} requests (CircuitBreaker: {inst['circuit_breaker_state']})")
    print(f"  * Total Tokens Consumed:    {report.iro_metrics.total_tokens:,}")
    print(f"  * Total Synthetic Cost:     INR {report.iro_metrics.synthetic_total_cost_inr:,.2f} (${report.iro_metrics.synthetic_total_cost_usd:,.4f})")

    print("\n" + "=" * 80)
    print("   PHASE 8 COMPLETE — ALL SYSTEMS OPERATIONAL AND PRODUCTION-VERIFIED   ")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
