"""SSLCOMMERZ payment adapter for University Connect.

Credentials are environment variables. No secrets are stored in source control.
The adapter implements hosted checkout initiation and server-side validation;
subscription entitlement changes should happen only after validation.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal
from typing import Any

import requests


class PaymentConfigurationError(RuntimeError):
    pass


class SSLCommerzClient:
    def __init__(self) -> None:
        self.store_id = os.getenv("SSLCOMMERZ_STORE_ID")
        self.store_password = os.getenv("SSLCOMMERZ_STORE_PASSWORD")
        self.sandbox = os.getenv("SSLCOMMERZ_SANDBOX", "true").lower() in {"1", "true", "yes", "on"}
        if not self.store_id or not self.store_password:
            raise PaymentConfigurationError("SSLCOMMERZ_STORE_ID and SSLCOMMERZ_STORE_PASSWORD are required.")
        self.base = "https://sandbox.sslcommerz.com" if self.sandbox else "https://securepay.sslcommerz.com"

    def initiate(self, *, amount: Decimal, currency: str, customer: dict[str, str], callbacks: dict[str, str], product_category: str = "subscription") -> dict[str, Any]:
        if amount < Decimal("10.00"):
            raise ValueError("SSLCOMMERZ transactions must be at least 10.00.")
        tran_id = f"UC-{uuid.uuid4().hex[:24]}"
        data = {
            "store_id": self.store_id,
            "store_passwd": self.store_password,
            "total_amount": f"{amount:.2f}",
            "currency": currency,
            "tran_id": tran_id,
            "product_category": product_category,
            "success_url": callbacks["success"],
            "fail_url": callbacks["fail"],
            "cancel_url": callbacks["cancel"],
            "ipn_url": callbacks["ipn"],
            "cus_name": customer.get("name", "University Connect customer"),
            "cus_email": customer.get("email", ""),
            "cus_add1": customer.get("address", "Bangladesh"),
            "cus_city": customer.get("city", "Dhaka"),
            "cus_country": customer.get("country", "Bangladesh"),
            "shipping_method": "NO",
            "num_of_item": "1",
        }
        response = requests.post(f"{self.base}/gwprocess/v4/api.php", data=data, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("GatewayPageURL"):
            raise RuntimeError(payload.get("failedreason") or "Payment session could not be created.")
        return {"transaction_id": tran_id, "gateway_url": payload["GatewayPageURL"], "raw": payload}

    def validate(self, val_id: str) -> dict[str, Any]:
        response = requests.get(
            f"{self.base}/validator/api/validationserverAPI.php",
            params={"val_id": val_id, "store_id": self.store_id, "store_passwd": self.store_password, "v": 1, "format": "json"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "VALID" or payload.get("validated_on") is None:
            raise RuntimeError("Payment validation failed.")
        return payload
