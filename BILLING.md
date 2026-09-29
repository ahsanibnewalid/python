# Optional billing

University Connect is **free by default**. Website owners are not required to create a payment account, enter merchant credentials, or enable checkout.

## Free mode

Leave these unset (or use the defaults):

```env
BILLING_ENABLED=false
BILLING_PROVIDER=none
```

The application remains usable without any payment gateway.

## Enable payments

Only a site owner who wants to sell paid plans should enable billing:

```env
BILLING_ENABLED=true
BILLING_PROVIDER=sslcommerz
BILLING_MODE=sandbox
SSLCOMMERZ_STORE_ID=...
SSLCOMMERZ_STORE_PASSWORD=...
```

Use `BILLING_MODE=live` only after the merchant account and production callback/IPN configuration are ready. SSLCOMMERZ requires server-side initiation and transaction validation; its IPN flow is intended to recover payments even when a customer's browser cannot return to the site. See the official integration documentation: https://developer.sslcommerz.com/doc/v4/.

## Product policy

Billing must never be required merely to run the platform. A deployment owner chooses whether University Connect is:

- completely free;
- monetized with paid student/recruiter features; or
- sold as an institutional SaaS with university subscriptions.

Provider-specific credentials must stay in environment secrets and must never be committed to Git.
