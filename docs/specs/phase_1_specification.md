# Phase 1 Specification (Revised): Payment Domain & Scenario-Aware Synthetic Data

**Project**: Intelligent Recovery Orchestrator (IRO)  
**Track**: Razorpay AI Buildathon 2026 - Track 3: AI Revenue Recovery  
**Version**: 1.1.0 (Revised Specification)

---

## 1. Core Architectural Tenet: PostgreSQL as Durable Source of Truth

- **PostgreSQL is the authoritative, durable system of record** for all payment transactions, attempts, failure diagnoses, recovery cases, and state transitions.
- **Redis (Phase 2)** is strictly an ephemeral concurrency and caching layer:
  - Distributed locks (`SET NX EX`)
  - Worker TTLs
  - Idempotency deduplication tokens
  - Hot route-health snapshots
- **Rule**: Redis must **never** become the authoritative store for payment lifecycle state. All recovery decisions and state mutations must be durably committed to PostgreSQL.

---

## 2. Refined Domain Entities Specification

### 2.1 `PaymentMethod` (Enum / Value Type)
`PaymentMethod` is an immutable **enum/value type**, not a database entity table, avoiding unnecessary relational joins:
```text
PaymentMethod
├── UPI
├── CREDIT_CARD
├── DEBIT_CARD
├── NETBANKING
└── WALLET
```

---

### 2.2 `PaymentFailure` & Explicit Failure Taxonomy
A failed attempt must be paired with structured diagnostic classification to enable deterministic routing in Phase 3/4.

#### Failure Category Enum:
```text
FailureCategory
├── TRANSIENT                  # Network timeouts, gateway timeouts, rate limits -> usually recoverable via backoff
├── ROUTE_DEGRADATION          # Bank switch down, circuit breaker open -> usually recoverable via alternate route
├── CUSTOMER_ACTION_REQUIRED   # Insufficient funds, limit exceeded, user dropout -> do NOT blindly retry; require customer action
├── PERMANENT                  # Invalid VPA, card expired, account closed -> non-recoverable
└── FRAUD                      # Velocity limit, fraud risk engine trigger -> non-recoverable; immediate STOP
```

#### Taxonomy Mapping:
| Category | Example Error Codes | Recoverable | Default Recovery Action |
| :--- | :--- | :--- | :--- |
| **`TRANSIENT`** | `GATEWAY_TIMEOUT`, `NETWORK_ERROR`, `RATE_LIMIT_EXCEEDED` | `True` | Scheduled backoff retry |
| **`ROUTE_DEGRADATION`** | `BANK_DOWNTIME`, `SWITCH_UNAVAILABLE` | `True` | Alternate route failover |
| **`CUSTOMER_ACTION_REQUIRED`** | `INSUFFICIENT_FUNDS`, `LIMIT_EXCEEDED`, `USER_DROPPED_OFF` | `True` | Customer link / alternate method prompt (no blind retry) |
| **`PERMANENT`** | `AUTHENTICATION_FAILED`, `CARD_EXPIRED`, `INVALID_VPA`, `ACCOUNT_BLOCKED` | `False` | Terminate recovery (`STOPPED`) |
| **`FRAUD`** | `FRAUD_SUSPECTED`, `VELOCITY_CHECK_FAILED` | `False` | Terminate & flag (`STOPPED`) |

#### Entity Attributes (`payment_failures`):
- `id`: UUID (Primary Key)
- `attempt_id`: UUID (FK -> `payment_attempts.id`, Unique 1:1)
- `payment_id`: UUID (FK -> `payments.id`, Indexed)
- `failure_category`: `FailureCategory` (Indexed)
- `error_code`: String (Indexed, e.g. `GATEWAY_TIMEOUT`)
- `reason`: Text (Diagnostic explanation from bank/gateway)
- `recoverable`: Boolean (Indexed)
- `suggested_backoff_sec`: Integer
- `detected_at`: Timestamp (UTC)

---

### 2.3 `RecoveryCase` (1-to-0..1 Strict Relationship)
Each payment has at most **one** recovery case (`Payment 1 ─── 0..1 RecoveryCase`). Multiple recovery workers or processes can never instantiate competing cases for the same payment.

#### Entity Attributes (`recovery_cases`):
- `id`: UUID (Primary Key)
- `payment_id`: UUID (FK -> `payments.id`, **UNIQUE CONSTRAINT**)
- `status`: `PaymentLifecycleState` (Indexed)
- `strategy`: `RecoveryStrategy`
- `attempt_count`: Integer (default 0)
- `max_attempts`: Integer (default 2)
- `started_at`: Timestamp (UTC, nullable)
- `completed_at`: Timestamp (UTC, nullable)
- `stop_reason`: String (nullable, e.g. `MAX_RETRIES_EXCEEDED`, `FRAUD_DETECTED`, `RECOVERED_SUCCESS`)
- `estimated_recovery_rate`: Float (nullable)
- `recovered_amount_inr`: Numeric(12, 2) (default 0.00)

---

### 2.4 Supporting Entities
- **`Merchant`**: `id`, `name`, `mcc`, `tier`, `recovery_enabled`, `max_auto_retries`, `max_recovery_amount_inr`, `auto_escalate_threshold_inr`.
- **`Customer`**: `id`, `external_id`, `email_masked`, `phone_masked`, `historical_success_rate` $[0.0, 1.0]$, `risk_score` $[0.0, 1.0]$.
- **`PaymentRoute`**: `id`, `name`, `payment_method`, `provider`, `health_score` $[0.0, 1.0]$, `avg_latency_ms`, `is_active`, `status` (`HEALTHY`, `DEGRADED`, `DOWN`).
- **`Payment`**: `id`, `merchant_id`, `customer_id`, `amount_inr`, `currency`, `payment_method`, `preferred_route_id`, `status`, `idempotency_key` (Unique), `final_error_code`.
- **`PaymentAttempt`**: `id`, `payment_id`, `attempt_number`, `route_id`, `payment_method`, `status`, `gateway_ref_id`, `latency_ms`, Unique (`payment_id`, `attempt_number`).

---

## 3. Strict State Transition Matrix

The system enforces a formal transition graph. Terminal states are completely locked.

```text
CREATED
   ↓
PROCESSING
   ├── SUCCESS (Terminal)
   └── FAILED
          ├── STOPPED (Terminal - if permanent / fraud)
          └── RECOVERY_PENDING
                 ├── STOPPED (Terminal - if merchant policy forbids)
                 └── RECOVERING
                        ├── RECOVERED (Terminal)
                        ├── ESCALATED (Terminal for automated system)
                        ├── STOPPED (Terminal - if failure unrecoverable)
                        └── RECOVERY_PENDING (Re-queue after backoff delay)
```

### Transition Verification Matrix:
| From State | Allowed Target States | Disallowed / Illegal Transitions |
| :--- | :--- | :--- |
| **`CREATED`** | `PROCESSING`, `STOPPED` | `SUCCESS`, `FAILED`, `RECOVERED`, `RECOVERING`, `ESCALATED` |
| **`PROCESSING`** | `SUCCESS`, `FAILED` | `CREATED`, `RECOVERED`, `RECOVERING`, `ESCALATED` |
| **`SUCCESS`** | *None* (**Terminal**) | `FAILED` ❌, `PROCESSING` ❌, `RECOVERING` ❌ |
| **`FAILED`** | `RECOVERY_PENDING`, `STOPPED` | `SUCCESS` ❌, `RECOVERED` ❌, `RECOVERING` ❌ |
| **`RECOVERY_PENDING`** | `RECOVERING`, `STOPPED` | `SUCCESS` ❌, `RECOVERED` ❌, `CREATED` ❌ |
| **`RECOVERING`** | `RECOVERED`, `ESCALATED`, `STOPPED`, `RECOVERY_PENDING` | `CREATED` ❌, `SUCCESS` ❌ |
| **`RECOVERED`** | *None* (**Terminal**) | `RECOVERING` ❌, `FAILED` ❌, `STOPPED` ❌ |
| **`ESCALATED`** | *None* (**Terminal for automation**) | `RECOVERING` ❌, `RECOVERED` ❌ |
| **`STOPPED`** | *None* (**Terminal**) | `RECOVERING` ❌, `RECOVERY_PENDING` ❌ |

---

## 4. Scenario-Aware Synthetic Data Generator

In addition to probabilistic bulk generation, the generator supports **deterministic scenario presets** required for evaluation and demo benchmarks.

### Supported Presets:
1. **`--scenario healthy-transient`**:
   - UPI `GATEWAY_TIMEOUT` on a healthy route (`ROUTE_HDFC_UPI`, health 0.98).
   - High-trust customer (98% success rate, low risk 0.02).
   - Expected outcome: Deterministic backoff retry.

2. **`--scenario degraded-route`**:
   - UPI failure on degraded route (`ROUTE_SBI_UPI`, health 0.82, `BANK_DOWNTIME`).
   - Expected outcome: Gateway route failover to `ROUTE_HDFC_UPI`.

3. **`--scenario customer-action`**:
   - `INSUFFICIENT_FUNDS` or `LIMIT_EXCEEDED`.
   - Expected outcome: Categorized as `CUSTOMER_ACTION_REQUIRED`. Flags that blind retry is forbidden; prompts for notification/link.

4. **`--scenario repeated-failure`**:
   - Attempt 1 fails (`GATEWAY_TIMEOUT`), Attempt 2 fails (`BANK_DOWNTIME`).
   - High-value transaction (> INR 50,000).
   - Expected outcome: Moves to `ESCALATED` for human review or AI investigation.

5. **`--scenario fraud-stop`**:
   - `FRAUD_SUSPECTED` or velocity check failure on high-risk customer (risk > 0.70).
   - Expected outcome: Immediately transitions to `STOPPED`. Zero recovery retries.

6. **`--scenario max-retries`**:
   - Number of attempts equals `merchant.max_auto_retries`.
   - Expected outcome: Recovery stops; transitions to `STOPPED` or `ESCALATED`.

---

## 5. Phase 1 Boundary Enforcement
- **NO AI agents / LLMs** (Phase 6).
- **NO Kafka consumers / event backbone** (Phase 3).
- **NO real payment retry API execution** (Phase 5).
- **Focus**: PostgreSQL durable schema, 1:1 RecoveryCase, strict state machine, and scenario-aware synthetic data generator.
