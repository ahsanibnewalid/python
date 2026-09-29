"""Provider-neutral billing primitives for University Connect.

The application can sell three product surfaces:
- Student Pro: career/CV and discovery upgrades.
- Recruiter Pro: job publishing, candidate search and analytics.
- University: branded campus workspace and administration.

Payment providers are deliberately isolated. This keeps plan/entitlement
logic testable and allows a Bangladesh-friendly gateway to be enabled without
rewriting the application when merchant credentials are available.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
import os
from typing import Protocol


class BillingInterval(StrEnum):
    MONTH = "month"
    YEAR = "year"
    ONE_TIME = "one_time"


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    audience: str
    amount: Decimal
    currency: str
    interval: BillingInterval
    description: str
    features: tuple[str, ...]


PLANS: dict[str, Plan] = {
    "student_pro": Plan(
        "student_pro", "Student Pro", "student", Decimal("299"), "BDT",
        BillingInterval.MONTH, "Career tools for ambitious students and graduates.",
        ("Advanced CV profile", "Priority career discovery", "Profile analytics", "Featured portfolio"),
    ),
    "recruiter_pro": Plan(
        "recruiter_pro", "Recruiter Pro", "recruiter", Decimal("1999"), "BDT",
        BillingInterval.MONTH, "Hiring tools for growing teams.",
        ("Unlimited job drafts", "Candidate search", "Applicant analytics", "Recruiter branding"),
    ),
    "university": Plan(
        "university", "University", "university", Decimal("9999"), "BDT",
        BillingInterval.MONTH, "A branded digital campus for institutions.",
        ("Branded campus", "Department administration", "Announcements & events", "Institution analytics"),
    ),
}


class PaymentProvider(Protocol):
    def create_checkout(self, *, plan: Plan, customer_id: str, return_url: str) -> str: ...


class BillingError(RuntimeError):
    pass


class MissingProviderCredentials(BillingError):
    pass


def configured_provider() -> str:
    return os.getenv("BILLING_PROVIDER", "manual").strip().lower()


def get_plan(key: str) -> Plan:
    try:
        return PLANS[key]
    except KeyError as exc:
        raise BillingError(f"Unknown billing plan: {key}") from exc


def feature_enabled(plan_key: str | None, feature: str) -> bool:
    """Single entitlement check used by routes/services, not templates."""
    if not plan_key:
        return False
    plan = PLANS.get(plan_key)
    if not plan:
        return False
    return feature in plan.features


def provider_status() -> dict[str, str | bool]:
    provider = configured_provider()
    if provider == "sslcommerz":
        ready = bool(os.getenv("SSLCOMMERZ_STORE_ID") and os.getenv("SSLCOMMERZ_STORE_PASSWORD"))
        return {"provider": provider, "configured": ready}
    if provider == "moneybag":
        return {"provider": provider, "configured": bool(os.getenv("MONEYBAG_MERCHANT_API_KEY"))}
    return {"provider": provider, "configured": provider == "manual"}
