# University Connect SaaS launch checklist

## Product
- [x] Professional/campus SaaS design system
- [x] Social feed and profile experience
- [x] Messaging, groups, campus and admin screen styling
- [x] Monetization model and entitlement architecture
- [ ] End-to-end subscription UI verified against production database

## Payments
- [x] Provider-neutral payment service boundary
- [x] SSLCOMMERZ hosted checkout adapter
- [x] Server-side transaction validation adapter
- [ ] Create SSLCOMMERZ merchant account
- [ ] Add production store ID/password as deployment secrets
- [ ] Configure public HTTPS IPN/success/fail/cancel URLs
- [ ] Complete a sandbox transaction
- [ ] Complete a production transaction before launch

SSLCOMMERZ requires a merchant store ID/password, server-side initiation, an IPN listener and transaction validation before a payment should be treated as valid. See https://sslcommerz.com/integration-document/.

## Production infrastructure
- [ ] PostgreSQL production database
- [ ] Redis for cache/rate limits/background jobs
- [ ] Object storage for user media
- [ ] SMTP/email provider
- [ ] HTTPS domain and trusted proxy configuration
- [ ] Secret management
- [ ] Automated backups and restore test
- [ ] Error monitoring and structured logs
- [ ] CI/CD deployment environment

## Security
- [x] CSRF protection retained
- [x] Server-side authentication boundaries
- [ ] Full object-level authorization audit
- [ ] Upload content validation and malware scanning
- [ ] Rate-limit login, messaging and expensive endpoints
- [ ] Security headers/CSP audit
- [ ] Production dependency vulnerability scan

## Commercial readiness
- [ ] Terms of Service
- [ ] Privacy Policy
- [ ] Refund/cancellation policy
- [ ] Support/contact workflow
- [ ] Invoice/receipt workflow
- [ ] Subscription cancellation/downgrade workflow
- [ ] Admin revenue/transaction reconciliation
