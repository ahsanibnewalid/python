# University Connect SaaS monetization

## Product model

University Connect is positioned as a multi-sided SaaS platform rather than an ad-first social network.

### 1. Student Pro — BDT 299/month
Free student accounts remain useful. Paid value comes from career outcomes:
- enhanced CV/profile
- career discovery
- profile analytics
- portfolio highlighting

### 2. Recruiter Pro — BDT 1,999/month
Employers pay for hiring workflow value:
- job publishing
- candidate discovery
- applicant analytics
- employer branding

### 3. University — BDT 9,999/month
Institutions pay for operating a branded digital campus:
- university/department administration
- announcements and events
- student/alumni community
- institutional analytics

These are initial defaults, not guaranteed market prices. Pricing should be tested with real customers.

## Billing architecture

Plan definitions live in `billing.py` and entitlement checks are server-side. Payment provider credentials are never stored in templates or client JavaScript.

The provider layer is intentionally replaceable. The production provider should be selected with `BILLING_PROVIDER` and credentials supplied through deployment secrets.

For a Bangladesh-based merchant, do not build around an assumed Stripe account. Stripe does not currently list Bangladesh as a supported merchant country. SSLCOMMERZ provides documented hosted checkout, IPN and transaction validation; other Bangladesh payment providers may be added through the same provider interface. Provider approval, recurring-payment capability and settlement terms must be confirmed with the merchant account before launch.

## Launch gates

A paid plan must not be considered live until all of these are verified:

1. Merchant account approved.
2. Sandbox checkout succeeds.
3. Server-to-server payment notification is idempotent.
4. Transaction amount and currency are validated server-side.
5. Successful payment creates/updates an entitlement.
6. Failed/cancelled payments do not grant entitlement.
7. Refund/revocation path is tested.
8. Webhook/IPN endpoint is rate-limited and logged without secrets.
9. Subscription renewal behavior is confirmed in writing by the provider.
10. Customer invoices/receipts and cancellation rules are documented.

## Revenue expansion after core SaaS

Only after the core product has repeat usage should additional revenue streams be considered:
- sponsored employer listings
- promoted jobs
- university recruitment campaigns
- premium campus branding/custom domains
- paid career events
- enterprise API/data integrations with privacy controls

The platform should never require advertising or sale of student personal data for the core business model.
