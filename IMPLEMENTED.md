# University Connect v2 implementation

This package is the practical first-pass implementation of the previously discussed roadmap.

## Implemented now

1. Security hardening
   - `.env` is loaded when `python app.py` is used directly.
   - Production refuses to start without a Flask secret and admin password hash.
   - Debug mode remains off.
   - CSRF is enforced on state-changing routes, including messaging and admin profile actions.
   - Basic image magic-byte validation was added for image uploads.
   - Security headers, secure-cookie switches and HTTPS enforcement remain enabled by configuration.
   - Friendly 400/403/404/413/500 pages were added.

2. Admin user management
   - New `/admin/users` page.
   - Search by name, username, Gmail or phone.
   - View/edit user profile.
   - Delete user with CSRF protection and activity logging.
   - Dashboard now links to User Management.

3. Commercial foundation
   - Universities
   - Roles and user-role mapping
   - Announcements
   - Clubs and club membership
   - Events and registrations
   - Notifications
   - Subscriptions

4. Developer/production packaging
   - Better README
   - `.env.example`
   - `.gitignore`
   - Gunicorn dependency
   - Basic tests

## Deliberately not auto-migrated yet

- PostgreSQL migration: the current SQLite MVP is working and a safe production migration needs a deliberate schema/data migration and deployment database URL.
- Strict multi-tenant authorization: adding a `university_id` column and tables is only the foundation; every query and route must be tenant-scoped before multiple real institutions share one deployment.
- Payment processing: a payment provider, webhook signing secret, tax/billing rules and account configuration are required.
- Object storage/CDN: requires a provider and credentials.
- Malware scanning/video transcoding: requires infrastructure/services beyond the current Flask process.

These items are intentionally left as explicit next architecture steps rather than pretending that a schema-only change provides full production multi-tenancy or billing.
