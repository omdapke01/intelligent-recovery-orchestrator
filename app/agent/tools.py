"""Read-only database tools and tool registry for the Specialist Recovery Agent.

Invariants:
1. STRICT READ-ONLY ENFORCEMENT: Queries use SELECT only. Zero INSERT, UPDATE, or DELETE operations.
2. ZERO FINANCIAL MUTATION: Tools cannot alter payment status, create attempts, or call providers.
3. UNTRUSTED DATA BOUNDARY: String outputs from DB are normalized and marked as data, never instructions.
4. DUPLICATE CALL PREVENTION: The registry tracks call signatures and prevents looping on identical queries.
"""

from datetime import datetime, timezone
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.schemas import ToolCallRecord
from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.payment_attempt import PaymentAttempt
from app.models.payment_failure import PaymentFailure
from app.models.payment_route import PaymentRoute

logger = logging.getLogger("iro.agent.tools")


# Whitelist of permitted investigation tools
PERMITTED_AGENT_TOOLS: Set[str] = {
    "getPayment",
    "getPaymentAttempts",
    "getCustomerProfile",
    "getFailureHistory",
    "getMerchantRecoveryPolicy",
    "getRouteHealth",
}


async def get_payment(session: AsyncSession, payment_id: uuid.UUID | str) -> Dict[str, Any]:
    """Retrieve core payment record details. Read-only."""
    if isinstance(payment_id, str):
        try:
            payment_id = uuid.UUID(payment_id)
        except ValueError:
            return {"error": f"Invalid UUID format: {payment_id}"}

    stmt = select(Payment).where(Payment.id == payment_id)
    res = await session.execute(stmt)
    payment = res.scalar_one_or_none()

    if not payment:
        return {"error": f"Payment '{payment_id}' not found"}

    return {
        "payment_id": str(payment.id),
        "merchant_id": str(payment.merchant_id),
        "customer_id": str(payment.customer_id),
        "amount_inr": float(payment.amount_inr),
        "currency": payment.currency,
        "payment_method": payment.payment_method.value,
        "status": payment.status.value,
        "preferred_route_id": payment.preferred_route_id,
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
        "updated_at": payment.updated_at.isoformat() if payment.updated_at else None,
    }


async def get_payment_attempts(session: AsyncSession, payment_id: uuid.UUID | str) -> List[Dict[str, Any]]:
    """Retrieve chronological history of payment attempts. Read-only."""
    if isinstance(payment_id, str):
        try:
            payment_id = uuid.UUID(payment_id)
        except ValueError:
            return [{"error": f"Invalid UUID format: {payment_id}"}]

    stmt = (
        select(PaymentAttempt)
        .where(PaymentAttempt.payment_id == payment_id)
        .order_by(PaymentAttempt.attempt_number.asc())
    )
    res = await session.execute(stmt)
    attempts = res.scalars().all()

    return [
        {
            "attempt_id": str(att.id),
            "attempt_number": att.attempt_number,
            "route_id": att.route_id,
            "status": att.status.value,
            "latency_ms": att.latency_ms,
            "gateway_ref_id": att.gateway_ref_id,
            "idempotency_key": att.idempotency_key,
            "created_at": att.created_at.isoformat() if att.created_at else None,
        }
        for att in attempts
    ]


async def get_customer_profile(session: AsyncSession, customer_id: uuid.UUID | str) -> Dict[str, Any]:
    """Retrieve customer risk profile and historical payment reliability. Read-only."""
    if isinstance(customer_id, str):
        try:
            customer_id = uuid.UUID(customer_id)
        except ValueError:
            return {"error": f"Invalid UUID format: {customer_id}"}

    stmt = select(Customer).where(Customer.id == customer_id)
    res = await session.execute(stmt)
    customer = res.scalar_one_or_none()

    if not customer:
        return {"error": f"Customer '{customer_id}' not found"}

    return {
        "customer_id": str(customer.id),
        "external_id": customer.external_id,
        "email_masked": customer.email_masked,
        "phone_masked": customer.phone_masked,
        "historical_success_rate": customer.historical_success_rate,
        "total_transactions": customer.total_transactions,
        "risk_score": customer.risk_score,
        "risk_classification": "LOW_RISK" if customer.risk_score < 0.20 else ("MEDIUM_RISK" if customer.risk_score < 0.60 else "HIGH_RISK"),
    }


async def get_failure_history(session: AsyncSession, payment_id: uuid.UUID | str) -> List[Dict[str, Any]]:
    """Retrieve granular diagnostic failure records for this payment. Read-only."""
    if isinstance(payment_id, str):
        try:
            payment_id = uuid.UUID(payment_id)
        except ValueError:
            return [{"error": f"Invalid UUID format: {payment_id}"}]

    stmt = (
        select(PaymentFailure)
        .where(PaymentFailure.payment_id == payment_id)
        .order_by(PaymentFailure.detected_at.desc())
    )
    res = await session.execute(stmt)
    failures = res.scalars().all()

    # Sanitize and untrust string reason codes to prevent indirect prompt injection
    return [
        {
            "failure_id": str(fail.id),
            "attempt_id": str(fail.attempt_id),
            "failure_category": fail.failure_category.value,
            "error_code": fail.error_code,
            # Data only: normalize and truncate untrusted text
            "reason_summary": (fail.reason or "")[:300].replace("\n", " "),
            "recoverable": fail.recoverable,
            "suggested_backoff_sec": fail.suggested_backoff_sec,
            "detected_at": fail.detected_at.isoformat() if fail.detected_at else None,
        }
        for fail in failures
    ]


async def get_merchant_recovery_policy(session: AsyncSession, merchant_id: uuid.UUID | str) -> Dict[str, Any]:
    """Retrieve merchant recovery preferences, limits, and escalation rules. Read-only."""
    if isinstance(merchant_id, str):
        try:
            merchant_id = uuid.UUID(merchant_id)
        except ValueError:
            return {"error": f"Invalid UUID format: {merchant_id}"}

    stmt = select(Merchant).where(Merchant.id == merchant_id)
    res = await session.execute(stmt)
    merchant = res.scalar_one_or_none()

    if not merchant:
        return {"error": f"Merchant '{merchant_id}' not found"}

    return {
        "merchant_id": str(merchant.id),
        "merchant_name": merchant.name,
        "tier": merchant.tier.value,
        "recovery_enabled": merchant.recovery_enabled,
        "max_auto_retries": merchant.max_auto_retries,
        "min_recovery_amount_inr": float(merchant.min_recovery_amount_inr),
        "auto_escalate_threshold_inr": float(merchant.auto_escalate_threshold_inr),
        "max_recovery_amount_inr": float(merchant.max_recovery_amount_inr),
    }


async def get_route_health(session: AsyncSession, route_id: str) -> Dict[str, Any]:
    """Retrieve operational health, latency, and status for a payment route. Read-only."""
    if not route_id:
        return {"error": "Route ID cannot be empty"}

    stmt = select(PaymentRoute).where(PaymentRoute.id == route_id)
    res = await session.execute(stmt)
    route = res.scalar_one_or_none()

    if not route:
        return {"error": f"Route '{route_id}' not found"}

    return {
        "route_id": route.id,
        "name": route.name,
        "payment_method": route.payment_method.value,
        "provider": route.provider,
        "health_score": route.health_score,
        "avg_latency_ms": route.avg_latency_ms,
        "is_active": route.is_active,
        "status": route.status.value,
    }


class ReadOnlyToolRegistry:
    """Registry managing read-only tool invocation, signature tracking,

    duplicate call prevention, and execution metrics.
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._executed_signatures: Set[str] = set()
        self._tool_history: List[ToolCallRecord] = []

        self._tools: Dict[str, Callable] = {
            "getPayment": get_payment,
            "getPaymentAttempts": get_payment_attempts,
            "getCustomerProfile": get_customer_profile,
            "getFailureHistory": get_failure_history,
            "getMerchantRecoveryPolicy": get_merchant_recovery_policy,
            "getRouteHealth": get_route_health,
        }

    @property
    def executed_signatures(self) -> Set[str]:
        return self._executed_signatures

    @property
    def tool_history(self) -> List[ToolCallRecord]:
        return self._tool_history

    def get_tool_names(self) -> List[str]:
        return list(self._tools.keys())

    def is_duplicate(self, tool_name: str, arguments: Dict[str, Any]) -> bool:
        """Check if an identical tool call was already made in this investigation."""
        sig = f"{tool_name}:{sorted(arguments.items())}"
        return sig in self._executed_signatures

    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool with strict read-only boundary, duplicate detection, and latency recording."""
        if tool_name not in self._tools:
            logger.warning(f"[UNAUTHORIZED TOOL] Tool '{tool_name}' is not in permitted toolset.")
            return {"error": f"Tool '{tool_name}' is not authorized or does not exist"}

        # Duplicate call prevention
        sig = f"{tool_name}:{sorted(arguments.items())}"
        if sig in self._executed_signatures:
            logger.info(f"[DUPLICATE TOOL DETECTED] Tool '{tool_name}' with args {arguments} already called.")
            return {
                "warning": f"Tool '{tool_name}' was already executed with these arguments in this investigation.",
                "cached": True,
            }

        self._executed_signatures.add(sig)
        tool_fn = self._tools[tool_name]

        t0 = time.perf_counter()
        try:
            result = await tool_fn(self._session, **arguments)
            latency_ms = round((time.perf_counter() - t0) * 1000, 2)

            record = ToolCallRecord(
                tool_name=tool_name,
                arguments=arguments,
                output_summary=result if isinstance(result, dict) else {"count": len(result)},
                latency_ms=latency_ms,
                status="SUCCESS",
                error=None,
            )
            self._tool_history.append(record)
            return result

        except Exception as err:
            latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            logger.error(f"[TOOL EXECUTION ERROR] Tool '{tool_name}' failed: {err}")

            record = ToolCallRecord(
                tool_name=tool_name,
                arguments=arguments,
                output_summary={},
                latency_ms=latency_ms,
                status="ERROR",
                error=str(err),
            )
            self._tool_history.append(record)
            return {"error": f"Tool execution failed: {err}"}
