# Intelligent Recovery Orchestrator (IRO)
> **Razorpay AI Buildathon 2026 — Track 3: AI Revenue Recovery**  
> *"The system is in charge of the AI, not the AI in charge of the payment system."*

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Test Suite](https://img.shields.io/badge/tests-120%20passed-brightgreen.svg)]()
[![Architecture](https://img.shields.io/badge/architecture-8--phase%20production--grade-indigo.svg)]()
[![Financial Safety](https://img.shields.io/badge/safety-zero%20unsafe%20actions-emerald.svg)]()

---

## Executive Summary

Payment failures in payment infrastructure result in severe revenue loss and cart abandonment. However, naive automated retries cause double debits, retry storms, compliance breaches, and merchant policy violations.

The **Intelligent Recovery Orchestrator (IRO)** is a distributed, multi-tiered revenue recovery system engineered for enterprise payment platforms. It couples **bounded AI reasoning** with **hard, deterministic financial safety policies** to recover failed transactions with mathematical safety guarantees.

### Key Performance Highlights (100-Payment Dual-Engine Benchmark)
- **Recovery Rate Lift**: **+25.0%** absolute lift (**57.0%** IRO vs **32.0%** Naive Baseline) / **+78.1%** relative lift
- **Retryable Recovery Rate**: **88.5%** on true retryable failures
- **Incremental Revenue Recovered**: **+₹83,412.00** on synthetic cohort (+56.5% lift)
- **Unsafe Financial Actions**: **0 unsafe actions** by IRO (**24** unsafe double-debits/fraud retries blocked)
- **Financial ROI Multiplier**: **2,921x** (Incremental Revenue Recovered / Synthetic AI Inference Cost)
- **Test Suite**: **120/120 passing unit, integration, and property tests**

---

## System Architecture

```text
Payment Failure Event
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. Asynchronous Event Pipeline & Lifecycle State Machine     │
│    (Outbox Pattern, Idempotent Delivery, Dead Letter Queue) │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Hierarchical 3-Tier Recovery Decision Engine             │
│    ├── Tier 1: Deterministic Classifier (Zero AI, <1ms)     │
│    │           Healthy transient timeouts & obvious declines│
│    ├── Tier 2: AI Fast Classification (Model Gateway)       │
│    │           Standard ambiguity, route selection          │
│    └── Tier 3: Specialist Recovery Agent                    │
│                Bounded loop (max 5 tools), read-only access │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. L7 Production AI Serving Cluster (Phase 8)               │
│    ├── Model Router (Complexity Assessment)                 │
│    └── L7 Least-Connections Load Balancer                   │
│        ├── Pool [FAST_CLASSIFICATION] (3 Worker Instances)  │
│        ├── Pool [DEEP_REASONING]      (2 Worker Instances)  │
│        └── Pool [STRUCTURED_EXTRACTION] (1 Worker Instance) │
└──────────────────────────────┬──────────────────────────────┘
                               │ Advisory Recommendation Only
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Deterministic Dual-Guard Boundary (Financial Safety)     │
│    ├── Phase 3: Logical Recovery Safety Guard               │
│    │   (Max attempts, SLA window, route status, merchant)   │
│    └── Phase 7: Hard Financial Safety Policy Engine         │
│        (Amount caps, fraud limits, fail-closed invariant)   │
└──────────────────────────────┬──────────────────────────────┘
                               │ Authorized Recovery Plan
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. Distributed Execution & Settlement Engine                │
│    ├── Distributed Mutex (Redis Lock with Owner UUID + TTL) │
│    ├── Durable Idempotency Barrier (PostgreSQL Unique Key)  │
│    └── Exponential Jittered Backoff Engine                  │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. Cryptographic Tamper-Evident Audit Trail                 │
│    (SHA-256 Hash Chain: Prev Hash + Event + Decision + Sign)│
└─────────────────────────────────────────────────────────────┘
```

---

## Core Architectural Invariants

1. **The System Governs the AI**: The AI model gateway and specialist agent are strictly advisory. They possess **zero mutation credentials** and zero payment execution capability.
2. **Dual Independent Safety Guards**: An AI recommendation must pass through two independent deterministic filters:
   - *Phase 3 Logical Guard*: Verifies logical recovery eligibility and business rules.
   - *Phase 7 Financial Policy Engine*: Enforces absolute financial safety limits (amount caps, fraud thresholds, merchant opt-outs) and **fails closed**.
3. **Double-Debit Prevention**: Guaranteed by Redis distributed mutex locks + PostgreSQL unique constraint idempotency barriers.
4. **Bounded Agent Reasoning**: The Specialist Recovery Agent is bounded by `MAX_TOOL_CALLS = 5`, deterministic evidence sufficiency checks, and prompt-injection defense treating tool outputs as untrusted data.
5. **Tamper-Evident Auditability**: Every policy check, agent decision, and execution attempt emits an append-only audit event linked via a cryptographic SHA-256 hash chain.

---

## Phased Implementation Roadmap

| Phase | Module | Key Capabilities |
| :--- | :--- | :--- |
| **Phase 1** | **Payment Domain & Synthetic Generator** | Complete domain data model, Alembic migrations, streaming $O(1)$ memory synthetic generator (100k+ payments). |
| **Phase 2** | **Event Pipeline & State Machine** | Strict 9-state payment lifecycle state machine, asynchronous broker, Outbox pattern, Dead Letter Queue. |
| **Phase 3** | **Orchestrator Decision Engine** | 3-tier hierarchical engine, rule-based deterministic classifier, strategy selector, deterministic safety guard. |
| **Phase 4** | **Distributed Recovery Execution** | Redis distributed locking with auto-renewal, durable PostgreSQL idempotency barrier, exponential jittered backoff. |
| **Phase 5** | **AI Model Gateway & Prompt Defense** | Unified gateway, model routing across fast/deep/extraction tiers, strict Pydantic schema validation, tool whitelisting. |
| **Phase 6** | **Specialist Recovery Agent** | Bounded reasoning loop, read-only investigation tools (`getRouteHealth`, `getMerchantRecoveryPolicy`, etc.), prompt injection defense. |
| **Phase 7** | **Policy Engine & Hash-Chain Audit** | Deterministic financial safety policies, fail-closed authorization, append-only cryptographic SHA-256 hash chain audit trail. |
| **Phase 8** | **L7 AI Serving, Benchmark & Dashboard** | Horizontally scaled L7 AI load balancer (least-connections, circuit breakers), 100-case empirical evaluation benchmark, interactive HTML dashboard. |

---

## Benchmark Evaluation Results

Running the dual-engine benchmark (`scripts/demo_phase8_dashboard.py`) evaluates 100 synthetic payment failures spanning transient timeouts, route outages, customer-action-required, high-value invoices, fraud declines, stale captures, and in-flight pending payments:

```text
==============================================================================
   RAZORPAY INTELLIGENT RECOVERY ORCHESTRATOR (IRO) - BENCHMARK REPORT    
==============================================================================
Report ID: eval_rep_1824f5ba  |  Evaluated: 100 Payments  |  Time: 2026-09-05T11:32:39Z
------------------------------------------------------------------------------
Dimension                        | Naive Baseline     | IRO Engine          
------------------------------------------------------------------------------
Overall Recovery Rate            |             32.0% |               57.0%
Retryable Recovery Rate          |             52.5% |               88.5%
Recovered Payments Count         |                32 |                  57
Recovered Revenue                | INR   147,657.00 | INR     231,069.00
Unsafe Actions (Fraud/Debits)    |                24 |                   0
Human Escalations                |                 0 |                   7
Decision Latency (Excl. Gateway) |            0.0ms |             25.2ms
Synthetic Inference Cost         |          INR 0.00 | INR          28.56
------------------------------------------------------------------------------
INCREMENTAL PERFORMANCE LIFT & FINANCIAL ROI:
  * Absolute Recovery Rate Lift:  +25.0% percentage points
  * Relative Recovery Rate Lift:  +78.1% lift over baseline
  * Incremental Recovered Revenue: +INR 83,412.00 (+56.5%)
  * Unsafe Actions Blocked:       24 policy violations prevented
  * AI Financial ROI Multiplier:  2,921x (Recovered Revenue / AI Cost)
------------------------------------------------------------------------------
SEGMENTED BREAKDOWN BY PAYMENT METHOD:
  [UPI         ] Cases: 63  | Base:  34.9% | IRO:  57.1% (+22.2%) | Delta: +INR   53,184
  [CREDIT_CARD ] Cases: 13  | Base:  46.1% | IRO:  61.5% (+15.4%) | Delta: +INR   12,211
  [NETBANKING  ] Cases: 7   | Base:  14.3% | IRO:  85.7% (+71.4%) | Delta: +INR   11,289
  [DEBIT_CARD  ] Cases: 17  | Base:  17.6% | IRO:  41.2% (+23.5%) | Delta: +INR    6,728
------------------------------------------------------------------------------

L7 AI SERVING CLUSTER TELEMETRY:
  * Total Active Requests:    0
  * Total Dispatches Handled: 32
  * Fast Tier Dispatches:     10 (worker-fast-alpha: 10 requests)
  * Deep Tier Dispatches:     22 (worker-deep-reasoning-01: 22 requests)
  * Total Tokens Consumed:    20,800
  * Total Synthetic Cost:     INR 28.56 ($0.3360)
==============================================================================
```

### Generated Artifacts
- **Interactive HTML Dashboard**: [`dashboard.html`](dashboard.html) — self-contained visual dashboard with tier breakdowns, segmented performance tables, ROI calculations, and policy compliance indicators.
- **Canonical JSON Report**: [`iro_benchmark_report.json`](iro_benchmark_report.json) — machine-readable benchmark telemetry and execution records.

---

## Getting Started

### Prerequisites
- Python 3.11+
- Git

### 1. Installation
```powershell
# Clone the repository
git clone https://github.com/omdapke01/intelligent-recovery-orchestrator.git
cd intelligent-recovery-orchestrator

# Install dependencies
pip install -e ".[dev]"
```

### 2. Run the Full Test Suite
```powershell
pytest tests/ -v
```
All 120 tests will execute and pass, verifying data models, state machines, execution locks, idempotency, agent bounds, policy engines, and L7 load balancing.

### 3. Run the Production AI Serving & Benchmark Demo
```powershell
python scripts/demo_phase8_dashboard.py
```
This runs the complete L7 AI serving cluster, benchmark evaluation, and generates `dashboard.html` and `iro_benchmark_report.json`.

### 4. Interactive Demos by Phase
```powershell
# Phase 1: Streaming Synthetic Payment Failure Generator
python scripts/generate_synthetic_data.py --count 1000 --output data/synthetic_1k.jsonl

# Phase 2: Asynchronous Event Pipeline & Outbox Demo
python scripts/demo_event_pipeline.py

# Phase 3: Hierarchical Orchestrator Decision Engine Demo
python scripts/demo_recovery_orchestrator.py

# Phase 4: Redis Distributed Mutex & PostgreSQL Idempotency Barrier
python scripts/demo_recovery_execution.py

# Phase 5: AI Model Gateway & Tiered Routing Demo
python scripts/demo_ai_decision_layer.py

# Phase 6: Specialist Recovery Investigation Agent Demo
python scripts/demo_recovery_agent.py

# Phase 7: Financial Policy Engine & Hash-Chain Audit Demo
python scripts/demo_policy_and_audit.py
```

---

## Project Structure

```text
.
├── alembic/                      # Database migrations
├── app/
│   ├── agent/                    # Specialist Recovery Agent (Phase 6)
│   │   ├── investigator.py       # Bounded loop & evidence sufficiency
│   │   ├── schemas.py            # Decision trace & audit schemas
│   │   └── tools.py              # Read-only tool registry
│   ├── ai/                       # AI Model Serving Layer (Phase 5 & 8)
│   │   ├── gateway.py            # AI Model Gateway
│   │   ├── hierarchy.py          # 3-tier hierarchical decision engine
│   │   ├── instances.py          # ModelServiceInstance & Circuit Breakers
│   │   ├── load_balancer.py      # L7 Least-Connections Load Balancer
│   │   ├── router.py             # Task complexity classifier & router
│   │   ├── sanitizer.py          # Prompt defense & extraction
│   │   └── schemas.py            # Pydantic schemas for AI layer
│   ├── audit/                    # Tamper-Evident Hash Chain Audit (Phase 7)
│   ├── consumers/                # Asynchronous event consumers (Phase 2)
│   ├── dashboard/                # HTML & JSON benchmark generator (Phase 8)
│   ├── evaluation/               # Benchmark runner & comparison engine (Phase 8)
│   ├── events/                   # Event broker & retry processor (Phase 2)
│   ├── execution/                # Redis lock & PostgreSQL idempotency (Phase 4)
│   ├── lifecycle/                # Payment state machine & transitions (Phase 2)
│   ├── models/                   # Core payment domain models (Phase 1)
│   ├── orchestrator/             # Hierarchical orchestrator & guards (Phase 3)
│   ├── policy/                   # Hard Financial Safety Policy Engine (Phase 7)
│   ├── services/                 # Payment database services
│   └── synthetic/                # Streaming synthetic data generator (Phase 1)
├── data/                         # Data directory (.gitkeep)
├── docs/                         # Specifications and architecture docs
├── scripts/                      # Phase-by-phase runnable demos
├── tests/                        # 120 automated unit and integration tests
├── dashboard.html                # Standalone interactive HTML benchmark dashboard
├── iro_benchmark_report.json     # Canonical benchmark evaluation metrics
├── pyproject.toml                # Project packaging & dependency specifications
└── README.md                     # This documentation
```

---

## License
MIT License. Developed for the Razorpay AI Buildathon 2026.
