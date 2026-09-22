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

## V4 Commercial Campus Management

This build adds real application workflows rather than only database foundations:

- University creation and tenant records
- Department creation
- Batch creation
- Group creation with university-wide or department-only visibility
- Group membership join/leave
- User assignment to university, department and batch
- User role assignment (STUDENT, UNIVERSITY_ADMIN, DEPARTMENT_ADMIN, MODERATOR, TEACHER, ALUMNI, SUPER_ADMIN)
- Announcements scoped to a university or global
- Clubs and events scoped to a university or global
- Event registration counts
- Platform analytics dashboard
- SQLite database backup download for administrators
- Branding configuration and custom-domain configuration storage
- Multiple university administrators through the role table
- User-facing Groups section
- Campus content filtered by the user's university

### Commercial limitations still requiring production work

- Payment gateway/billing is not implemented yet.
- Custom-domain DNS/SSL provisioning is configuration-only; deployment automation is still required.
- Fine-grained permission enforcement needs to be applied to every university/depart­ment/moderator action before multi-tenant production use.
- PostgreSQL/object storage/WebSocket infrastructure should replace SQLite/local polling for production scale.


## Messenger optimization update
- Voice calling and video calling/WebRTC functionality has been removed from the chat UI and backend to reduce browser/device overhead.
- Private messaging, E2EE key handling, chat themes/wallpapers, disappearing messages, safety number, encrypted backup/restore, block/unblock, reporting, and profile access remain.
