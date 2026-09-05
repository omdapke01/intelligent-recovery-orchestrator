# Phase 2 Specification (Revised): Event-Driven Payment Pipeline

**Project**: Intelligent Recovery Orchestrator (IRO)  
**Track**: Razorpay AI Buildathon 2026 - Track 3: AI Revenue Recovery  
**Version**: 1.1.0 (Revised Specification)  
**Status**: **Awaiting User Review**

---

## 1. Architectural Baseline & Master Roadmap Alignment

### 1.1 Master Roadmap Phase Alignment
The project phases are strictly aligned as follows:
- **Phase 1**: Payment Domain & Synthetic Data Foundation *(Complete)*
- **Phase 2 (Current)**: Event-Driven Payment Pipeline (Kafka, Consumers, Retry/DLQ, Idempotency)
- **Phase 3**: Deterministic Decision Engine & Fast-Path Routing
- **Phase 4**: Concurrency Control (Redis Locks, TTLs, Retry Execution Sandbox)
- **Phase 5**: AI Model Gateway (Routing & Load Balancing)
- **Phase 6**: AI Agent (Reasoning Specialist)
- **Phase 7**: Policy Engine (Hard Authorization & Safety Boundary)
- **Phase 8**: Scaling, Observability & Recovery Dashboard

### 1.2 Technology & Runtime Decision
- **Implementation Language**: **Python 3.12+**
- **Framework & Persistence**: FastAPI, SQLAlchemy 2.0 (asyncio), PostgreSQL (durable store), Alembic, Pydantic v2.
- **Messaging Client**: **`aiokafka`** (asyncio-native Kafka client for Python) with a pluggable broker abstraction (`InMemoryEventBroker` for unit/integration tests and `KafkaEventBroker` for production).

---

## 2. Event Backbone Architecture & Consumer Groups

```text
                               Payment API / Service
                                         │
                                         ▼ (Publish)
                               ┌───────────────────┐
                               │  payment.events   │
                               └─────────┬─────────┘
                                         │
              ┌──────────────────────────┴──────────────────────────┐
              ▼                                                     ▼
  [Consumer Group: iro-recovery-group]               [Consumer Group: iro-notification-group]
      ┌─────────────────────────┐                         ┌───────────────────────────┐
      │    Recovery Consumer    │                         │   Notification Consumer   │
      └────────────┬────────────┘                         └─────────────┬─────────────┘
                   │                                                    │
                   ▼ (Atomic DB Tx)                                     ▼ (Atomic DB Tx)
      ┌─────────────────────────┐                         ┌───────────────────────────┐
      │  processed_events (lock)│                         │  processed_events (lock)  │
      │  + recovery_cases       │                         │  + notifications          │
      └────────────┬────────────┘                         └───────────────────────────┘
                   │
                   ▼ (If notification required)
            Emits derived event:
          notification.requested
        (Preserves correlation_id)
                   │
                   └────────────────────────────────────────────────────▲
```

### 2.1 Consumer-Group Semantics
1. **`iro-recovery-group`**:
   - Dedicated consumer group for recovery orchestrators.
   - Listens to `payment.failed`, `payment.succeeded`, `payment.retry_requested`.
   - Distributes partition consumption across worker instances for horizontal scaling.
2. **`iro-notification-group`**:
   - Dedicated consumer group for notification workers.
   - Listens to `notification.requested`.
   - Completely decoupled from internal payment failure structures.

---

## 3. Correlation Chains & Event Traceability

To support end-to-end tracing and auditability, every event maintains a lineage chain:
- **`correlation_id`**: Globally unique transaction identifier initiated at payment creation and **strictly preserved across all downstream derived events**.
- **`causation_id`**: The specific `event_id` that triggered this event, establishing a direct parent-child causal graph.

### 3.1 Standard Event Envelope Schema
```json
{
  "event_id": "uuid-v4",
  "event_type": "notification.requested",
  "version": "v1",
  "producer": "recovery-consumer",
  "timestamp": "2026-09-04T17:30:00.000Z",
  "correlation_id": "corr-uuid-v4",
  "causation_id": "parent-event-uuid-v4",
  "data": { ... }
}
```

### 3.2 Required Event Payloads (10 Contracts)
1. **`payment.created`**: `payment_id`, `merchant_id`, `customer_id`, `amount_inr`, `currency`, `payment_method`, `idempotency_key`
2. **`payment.failed`**: `payment_id`, `merchant_id`, `customer_id`, `amount_inr`, `payment_method`, `route_id`, `failure_category`, `error_code`, `reason`, `attempt_number`, `recoverable`
3. **`payment.retry_requested`**: `payment_id`, `recovery_case_id`, `attempt_number`, `target_route_id`, `strategy`
4. **`payment.succeeded`**: `payment_id`, `merchant_id`, `amount_inr`, `attempt_number`, `route_id`, `gateway_ref_id`
5. **`recovery.started`**: `recovery_case_id`, `payment_id`, `merchant_id`, `strategy`
6. **`recovery.completed`**: `recovery_case_id`, `payment_id`, `recovered_amount_inr`, `total_attempts`
7. **`recovery.failed`**: `recovery_case_id`, `payment_id`, `attempt_number`, `error_code`, `reason`
8. **`recovery.escalated`**: `recovery_case_id`, `payment_id`, `escalation_reason`, `amount_inr`
9. **`recovery.stopped`**: `recovery_case_id`, `payment_id`, `stop_reason`
10. **`notification.requested`**: `notification_id`, `customer_id`, `payment_id`, `channel` (`EMAIL`, `SMS`, `WHATSAPP`, `PUSH`), `template`, `payload`

---

## 4. Atomic Idempotency Boundaries in PostgreSQL

To eliminate partial-failure duplicates, deduplication and business side-effects occur inside an **atomic PostgreSQL transaction**:

### 4.1 Durable Schema: `processed_events`
- `event_id`: UUID
- `consumer_name`: String (e.g. `iro-recovery-group`, `iro-notification-group`)
- `event_type`: String
- `processed_at`: DateTime (UTC)
- **Primary Key**: `(event_id, consumer_name)`

### 4.2 Atomic Transaction Protocol
```sql
BEGIN;

-- 1. Attempt to register event processing
INSERT INTO processed_events (event_id, consumer_name, event_type, processed_at)
VALUES (:event_id, :consumer_name, :event_type, NOW())
ON CONFLICT (event_id, consumer_name) DO NOTHING;

-- 2. Check if this worker won the insertion
-- If row inserted (affected_rows == 1):
--     Execute business side effect (e.g. INSERT INTO recovery_cases ...)
-- Else:
--     Skip business logic (duplicate detected)

COMMIT;
```
Kafka offsets are committed **only after** this database transaction successfully commits.

---

## 5. Decoupled Notification Architecture

Rather than hardcoding `payment.failed -> Send Notification`:
1. `RecoveryConsumer` consumes `payment.failed`.
2. Inside its workflow, if customer notification is warranted, it emits a `notification.requested` event to Kafka:
   - Inherits `correlation_id` from `payment.failed`.
   - Sets `causation_id` = `payment.failed.event_id`.
3. `NotificationConsumer` (in `iro-notification-group`) listens exclusively for `notification.requested`:
   - Runs its own atomic idempotency check.
   - Durably writes to the `notifications` table in PostgreSQL (`QUEUED`).
   - Simulates delivery dispatch (`SENT`).

---

## 6. Dead-Letter Queue & Active Retry Processor

Kafka topics cannot delay messages on their own. We implement an **Active Retry Processor** (`RetryProcessor`):

```text
payment.events
      │
      ▼ (Consumer error)
payment.events.retry.1 (Header: x-retry-count=1, x-retry-timestamp=now+10s)
      │
      ▼ (Consumed by RetryProcessor)
[Wait until now >= x-retry-timestamp]
      │
      ▼ (Republish)
payment.events
```

### Protocol:
1. **Poisonous / Schema-Invalid Events**:
   - Immediately routed directly to `payment.events.DLQ` with diagnostic headers:
     - `x-death-reason`: `MALFORMED_EVENT_SCHEMA`
     - `x-failed-at`: ISO timestamp
2. **Transient Failures**:
   - Failure 1 $\rightarrow$ Published to `payment.events.retry.1` (`x-retry-timestamp` = now + 10s).
   - Failure 2 $\rightarrow$ Published to `payment.events.retry.2` (`x-retry-timestamp` = now + 30s).
   - Failure 3 $\rightarrow$ Published to `payment.events.DLQ` (`x-death-reason: EXCEEDED_MAX_RETRIES`).
3. **`RetryProcessor`**:
   - Continuously consumes from `payment.events.retry.1` and `payment.events.retry.2`.
   - Inspects `x-retry-timestamp`.
   - When elapsed, republishes event back to `payment.events`.

---

## 7. Local Demonstrable Workflows & Test Suite

### Workflow A: Recovery Event Flow
`payment.failed` $\rightarrow$ Kafka $\rightarrow$ `RecoveryConsumer` $\rightarrow$ Atomic PostgreSQL transaction (`processed_events` + `RecoveryCase` created in `RECOVERY_PENDING`).

### Workflow B: Decoupled Notification Flow
`payment.failed` $\rightarrow$ `RecoveryConsumer` emits `notification.requested` $\rightarrow$ Kafka $\rightarrow$ `NotificationConsumer` $\rightarrow$ Atomic PostgreSQL transaction (`processed_events` + `Notification` created).

### Test Suite (`tests/`):
- `test_event_contracts_and_correlation`: Serializes/deserializes all 10 contracts; validates preservation of `correlation_id` and `causation_id`.
- `test_atomic_idempotency_duplicate_event`: Tests that publishing the same `event_id` twice results in exactly one `RecoveryCase` and one `processed_events` entry.
- `test_consumer_group_independence`: Tests that `RecoveryConsumer` and `NotificationConsumer` receive independent copies of relevant events.
- `test_malformed_event_quarantine_dlq`: Verifies that malformed payloads route to `.DLQ` without blocking consumers.
- `test_retry_processor_backoff_and_dlq`: Verifies transient failure retries, delay enforcement by `RetryProcessor`, and final DLQ routing upon exhaustion.
