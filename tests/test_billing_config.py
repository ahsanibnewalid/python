import os

from services.billing import get_billing_config, validate_billing_configuration


def test_billing_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BILLING_ENABLED", raising=False)
    monkeypatch.delenv("BILLING_PROVIDER", raising=False)
    cfg = get_billing_config()
    assert cfg.enabled is False
    ok, message = validate_billing_configuration()
    assert ok is True
    assert "free mode" in message


def test_enabled_billing_requires_provider_credentials(monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "true")
    monkeypatch.setenv("BILLING_PROVIDER", "sslcommerz")
    monkeypatch.setenv("BILLING_MODE", "sandbox")
    monkeypatch.delenv("SSLCOMMERZ_STORE_ID", raising=False)
    monkeypatch.delenv("SSLCOMMERZ_STORE_PASSWORD", raising=False)
    ok, message = validate_billing_configuration()
    assert ok is False
    assert "credentials" in message
