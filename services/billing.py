"""Optional SaaS billing integration.

Billing is an opt-in capability. A website owner can run University Connect
entirely free by leaving BILLING_ENABLED=false. Provider credentials are only
required when billing is explicitly enabled.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BillingConfig:
    enabled: bool
    provider: str
    mode: str


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_billing_config() -> BillingConfig:
    return BillingConfig(
        enabled=_bool(os.getenv("BILLING_ENABLED"), False),
        provider=os.getenv("BILLING_PROVIDER", "none").strip().lower() or "none",
        mode=os.getenv("BILLING_MODE", "sandbox").strip().lower() or "sandbox",
    )


def billing_enabled() -> bool:
    """Return whether paid checkout is intentionally enabled by the owner."""
    return get_billing_config().enabled


def validate_billing_configuration() -> tuple[bool, str]:
    """Validate configuration without ever requiring payment in free mode."""
    cfg = get_billing_config()
    if not cfg.enabled:
        return True, "Billing is disabled; University Connect runs in free mode."
    if cfg.provider not in {"sslcommerz", "none"}:
        return False, f"Unsupported billing provider: {cfg.provider}"
    if cfg.provider == "none":
        return False, "BILLING_ENABLED=true requires BILLING_PROVIDER to be configured."
    if cfg.mode not in {"sandbox", "live"}:
        return False, "BILLING_MODE must be sandbox or live."
    if cfg.provider == "sslcommerz":
        if not os.getenv("SSLCOMMERZ_STORE_ID") or not os.getenv("SSLCOMMERZ_STORE_PASSWORD"):
            return False, "SSLCOMMERZ credentials are required only when billing is enabled."
    return True, "Billing configuration is valid."
