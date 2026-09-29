import os
import unittest
from decimal import Decimal
from unittest.mock import patch, Mock

from services.payments.sslcommerz import SSLCommerzClient


class PaymentAdapterTests(unittest.TestCase):
    def client(self):
        with patch.dict(os.environ, {"SSLCOMMERZ_STORE_ID": "test", "SSLCOMMERZ_STORE_PASSWORD": "secret", "SSLCOMMERZ_SANDBOX": "true"}):
            return SSLCommerzClient()

    @patch("services.payments.sslcommerz.requests.post")
    def test_initiate_returns_gateway_url(self, post):
        response = Mock()
        response.json.return_value = {"GatewayPageURL": "https://sandbox.example/checkout"}
        response.raise_for_status.return_value = None
        post.return_value = response
        result = self.client().initiate(
            amount=Decimal("299.00"), currency="BDT",
            customer={"name": "Test User", "email": "test@example.com"},
            callbacks={"success": "https://example.com/success", "fail": "https://example.com/fail", "cancel": "https://example.com/cancel", "ipn": "https://example.com/ipn"},
        )
        self.assertTrue(result["transaction_id"].startswith("UC-"))
        self.assertEqual(result["gateway_url"], "https://sandbox.example/checkout")
        self.assertEqual(post.call_args.kwargs["data"]["currency"], "BDT")

    @patch("services.payments.sslcommerz.requests.get")
    def test_validate_accepts_valid_transaction(self, get):
        response = Mock()
        response.json.return_value = {"status": "VALID", "validated_on": "2026-09-30"}
        response.raise_for_status.return_value = None
        get.return_value = response
        self.assertEqual(self.client().validate("VAL-1")["status"], "VALID")


if __name__ == "__main__":
    unittest.main()
