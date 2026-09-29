# University Connect revenue model

University Connect is designed as a multi-sided SaaS rather than an ad-only social network.

## Student Pro
Optional paid tier for advanced career tooling: premium CV/resume layouts, profile insights, job/application organization, higher media/storage limits and advanced discovery controls.

## Recruiter Pro
Paid recruiter workspace for job/internship publishing, candidate search, applicant management, branded company presence and recruiting analytics.

## University plans
Institutional subscriptions for verified university administration, announcements, department/community management, events, analytics, moderation and branded university spaces.

## Transaction principles
- Entitlements are granted server-side only.
- Payment callbacks never grant access merely because a browser returned to a success URL.
- Gateway transactions must be validated server-to-server.
- All money amounts are stored as Decimal-compatible values and every transaction has a unique merchant transaction ID.
- Cancellation/downgrade must be idempotent.
- Provider-specific code stays behind `services/payments/`.

For Bangladesh launch, SSLCOMMERZ provides hosted checkout, IPN and server-side order validation; production requires merchant credentials. https://sslcommerz.com/integration-document/
