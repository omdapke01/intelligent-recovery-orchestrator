"""Archetypes and baseline profiles for realistic synthetic payment generation."""

from decimal import Decimal
from typing import Any, Dict, List
from app.models.enums import FailureCategory, MerchantTier, PaymentMethod, RouteStatus

# Standard Merchant Archetypes
MERCHANT_PROFILES: List[Dict[str, Any]] = [
    {
        "name": "Zomato QuickBites",
        "mcc": "5814",  # Fast Food Restaurants
        "tier": MerchantTier.ENTERPRISE,
        "recovery_enabled": True,
        "max_auto_retries": 2,
        "max_recovery_amount_inr": Decimal("5000.00"),
        "auto_escalate_threshold_inr": Decimal("3000.00"),
        "allowed_methods": [PaymentMethod.UPI, PaymentMethod.CREDIT_CARD, PaymentMethod.DEBIT_CARD, PaymentMethod.WALLET],
    },
    {
        "name": "Croma Electronics",
        "mcc": "5732",  # Electronic Sales
        "tier": MerchantTier.ENTERPRISE,
        "recovery_enabled": True,
        "max_auto_retries": 3,
        "max_recovery_amount_inr": Decimal("150000.00"),
        "auto_escalate_threshold_inr": Decimal("50000.00"),
        "allowed_methods": [PaymentMethod.UPI, PaymentMethod.CREDIT_CARD, PaymentMethod.DEBIT_CARD, PaymentMethod.NETBANKING],
    },
    {
        "name": "Urban Company Services",
        "mcc": "7299",  # Miscellaneous Personal Services
        "tier": MerchantTier.GROWTH,
        "recovery_enabled": True,
        "max_auto_retries": 2,
        "max_recovery_amount_inr": Decimal("25000.00"),
        "auto_escalate_threshold_inr": Decimal("15000.00"),
        "allowed_methods": [PaymentMethod.UPI, PaymentMethod.CREDIT_CARD, PaymentMethod.DEBIT_CARD],
    },
    {
        "name": "Khatabook Pro Subscription",
        "mcc": "5734",  # Computer Software Stores (B2B SaaS)
        "tier": MerchantTier.GROWTH,
        "recovery_enabled": True,
        "max_auto_retries": 3,
        "max_recovery_amount_inr": Decimal("50000.00"),
        "auto_escalate_threshold_inr": Decimal("20000.00"),
        "allowed_methods": [PaymentMethod.UPI, PaymentMethod.NETBANKING, PaymentMethod.CREDIT_CARD],
    },
    {
        "name": "Aura Artisans Boutique",
        "mcc": "5651",  # Family Clothing Stores
        "tier": MerchantTier.STARTUP,
        "recovery_enabled": True,
        "max_auto_retries": 1,
        "max_recovery_amount_inr": Decimal("10000.00"),
        "auto_escalate_threshold_inr": Decimal("8000.00"),
        "allowed_methods": [PaymentMethod.UPI, PaymentMethod.CREDIT_CARD],
    },
]

# Standard Gateway Routes with Realistic Reliability Profiles
ROUTE_PROFILES: List[Dict[str, Any]] = [
    {
        "id": "ROUTE_HDFC_UPI",
        "name": "HDFC UPI Direct Switch",
        "payment_method": PaymentMethod.UPI,
        "provider": "HDFC",
        "health_score": 0.98,
        "avg_latency_ms": 120.0,
        "status": RouteStatus.HEALTHY,
    },
    {
        "id": "ROUTE_ICICI_UPI",
        "name": "ICICI UPI Gateway Hub",
        "payment_method": PaymentMethod.UPI,
        "provider": "ICICI",
        "health_score": 0.95,
        "avg_latency_ms": 160.0,
        "status": RouteStatus.HEALTHY,
    },
    {
        "id": "ROUTE_SBI_UPI",
        "name": "SBI Core Switch",
        "payment_method": PaymentMethod.UPI,
        "provider": "SBI",
        "health_score": 0.82,  # Degraded / prone to timeouts
        "avg_latency_ms": 480.0,
        "status": RouteStatus.DEGRADED,
    },
    {
        "id": "ROUTE_AXIS_CARDS",
        "name": "Axis Bank Visa/Mastercard Gateway",
        "payment_method": PaymentMethod.CREDIT_CARD,
        "provider": "AXIS",
        "health_score": 0.96,
        "avg_latency_ms": 210.0,
        "status": RouteStatus.HEALTHY,
    },
    {
        "id": "ROUTE_HDFC_DEBIT",
        "name": "HDFC Debit Rail",
        "payment_method": PaymentMethod.DEBIT_CARD,
        "provider": "HDFC",
        "health_score": 0.94,
        "avg_latency_ms": 230.0,
        "is_active": True,
        "status": RouteStatus.HEALTHY,
    },
    {
        "id": "ROUTE_RAZORPAY_NETBANKING",
        "name": "Razorpay Multi-Bank NetBanking Bridge",
        "payment_method": PaymentMethod.NETBANKING,
        "provider": "RAZORPAY",
        "health_score": 0.91,
        "avg_latency_ms": 320.0,
        "is_active": True,
        "status": RouteStatus.HEALTHY,
    },
    {
        "id": "ROUTE_PAYTM_WALLET",
        "name": "Paytm Wallet Direct Connect",
        "payment_method": PaymentMethod.WALLET,
        "provider": "PAYTM",
        "health_score": 0.97,
        "avg_latency_ms": 90.0,
        "is_active": True,
        "status": RouteStatus.HEALTHY,
    },
]

# Failure Taxonomy with Diagnostic Metadata & Recoverability
FAILURE_TAXONOMY: Dict[FailureCategory, List[Dict[str, Any]]] = {
    FailureCategory.TRANSIENT: [
        {
            "error_code": "GATEWAY_TIMEOUT",
            "reason": "Gateway timed out waiting for upstream bank response (504)",
            "message": "Gateway timed out waiting for upstream bank response (504)",
            "recoverable": True,
            "is_recoverable": True,
            "suggested_backoff_sec": 45,
            "weight": 0.50,
        },
        {
            "error_code": "NETWORK_ERROR",
            "reason": "TCP handshake / SSL negotiation failed with banking switch",
            "message": "TCP handshake / SSL negotiation failed with banking switch",
            "recoverable": True,
            "is_recoverable": True,
            "suggested_backoff_sec": 20,
            "weight": 0.35,
        },
        {
            "error_code": "RATE_LIMIT_EXCEEDED",
            "reason": "Bank throttle policy hit; temporary cool-off required",
            "message": "Bank throttle policy hit; temporary cool-off required",
            "recoverable": True,
            "is_recoverable": True,
            "suggested_backoff_sec": 60,
            "weight": 0.15,
        },
    ],
    FailureCategory.ROUTE_DEGRADATION: [
        {
            "error_code": "BANK_DOWNTIME",
            "reason": "Acquiring bank switch reports scheduled maintenance or unplanned outage",
            "message": "Acquiring bank switch reports scheduled maintenance or unplanned outage",
            "recoverable": True,
            "is_recoverable": True,
            "suggested_backoff_sec": 180,
            "weight": 0.60,
        },
        {
            "error_code": "SWITCH_UNAVAILABLE",
            "reason": "NPCI / card interchange link temporarily degraded",
            "message": "NPCI / card interchange link temporarily degraded",
            "recoverable": True,
            "is_recoverable": True,
            "suggested_backoff_sec": 120,
            "weight": 0.40,
        },
    ],
    FailureCategory.CUSTOMER_ACTION_REQUIRED: [
        {
            "error_code": "INSUFFICIENT_FUNDS",
            "reason": "Declined by issuing bank due to insufficient balance",
            "message": "Declined by issuing bank due to insufficient balance",
            "recoverable": True,  # Recoverable via customer nudge / alternate card; DO NOT blindly retry
            "is_recoverable": True,
            "suggested_backoff_sec": 300,
            "weight": 0.50,
        },
        {
            "error_code": "LIMIT_EXCEEDED",
            "reason": "Customer daily / per-transaction UPI or card limit reached",
            "message": "Customer daily / per-transaction UPI or card limit reached",
            "recoverable": True,
            "is_recoverable": True,
            "suggested_backoff_sec": 600,
            "weight": 0.30,
        },
        {
            "error_code": "USER_DROPPED_OFF",
            "reason": "User abandoned payment screen / OTP input expired",
            "message": "User abandoned payment screen / OTP input expired",
            "recoverable": True,  # Recoverable via notification recovery link
            "is_recoverable": True,
            "suggested_backoff_sec": 60,
            "weight": 0.20,
        },
    ],
    FailureCategory.PERMANENT: [
        {
            "error_code": "AUTHENTICATION_FAILED",
            "reason": "Incorrect MPIN, 3D Secure OTP, or CVV validation failed",
            "message": "Incorrect MPIN, 3D Secure OTP, or CVV validation failed",
            "recoverable": False,
            "is_recoverable": False,
            "suggested_backoff_sec": 0,
            "weight": 0.45,
        },
        {
            "error_code": "CARD_EXPIRED",
            "reason": "Card expiration date passed",
            "message": "Card expiration date passed",
            "recoverable": False,
            "is_recoverable": False,
            "suggested_backoff_sec": 0,
            "weight": 0.25,
        },
        {
            "error_code": "INVALID_VPA",
            "reason": "Virtual Payment Address does not exist or customer handle inactive",
            "message": "Virtual Payment Address does not exist or customer handle inactive",
            "recoverable": False,
            "is_recoverable": False,
            "suggested_backoff_sec": 0,
            "weight": 0.20,
        },
        {
            "error_code": "ACCOUNT_BLOCKED",
            "reason": "Issuing bank placed a hard regulatory / debit freeze on account",
            "message": "Issuing bank placed a hard regulatory / debit freeze on account",
            "recoverable": False,
            "is_recoverable": False,
            "suggested_backoff_sec": 0,
            "weight": 0.10,
        },
    ],
    FailureCategory.FRAUD: [
        {
            "error_code": "FRAUD_SUSPECTED",
            "reason": "Flagged by risk engine due to anomalous IP, velocity, or blacklisted card",
            "message": "Flagged by risk engine due to anomalous IP, velocity, or blacklisted card",
            "recoverable": False,
            "is_recoverable": False,
            "suggested_backoff_sec": 0,
            "weight": 0.80,
        },
        {
            "error_code": "VELOCITY_CHECK_FAILED",
            "reason": "Exceeded maximum attempts within 10-minute sliding window",
            "message": "Exceeded maximum attempts within 10-minute sliding window",
            "recoverable": False,
            "is_recoverable": False,
            "suggested_backoff_sec": 0,
            "weight": 0.20,
        },
    ],
}
