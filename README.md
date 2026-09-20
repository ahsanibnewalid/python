# University Connect

A Flask-based university community platform for student profiles, messaging, a private newsfeed, media, admin controls and the foundation for multi-university SaaS.

## What is included in this v2 package

- Secure environment-based admin credentials
- `.env` loading when running `python app.py` directly
- CSRF protection on state-changing admin/user routes
- HttpOnly/SameSite session cookies and production HTTPS switches
- Security headers and friendly error pages
- Admin **User Management** page with search, profile editing and deletion
- Password hashing with Werkzeug
- Upload extension + basic image magic-byte validation
- Activity logging
- Commercial foundation tables for universities, roles, announcements, clubs, events, notifications and subscriptions
- SQLite MVP database with additive migrations
- Gunicorn production dependency and deployment examples
- Basic automated tests

## Run locally

### Windows

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env
python setup_local.py
python app.py
```

Open:
- User site: `http://127.0.0.1:5000/user-login`
- Registration: `http://127.0.0.1:5000/register`
- Admin: `http://127.0.0.1:5000/login`
- Admin users: `http://127.0.0.1:5000/admin/users`

### Linux/macOS

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python setup_local.py
python app.py
```

## Admin credentials

The username defaults to `admin` unless `ADMIN_USER` is changed. The password is the one you choose during local setup; it is stored only as a password hash in `.env`. There is no production `password123` fallback.

## Production

Use **Nginx → Gunicorn → Flask**. Do not expose Flask's development server to the internet. Set:

```text
APP_ENV=production
FLASK_SECRET_KEY=<long-random-secret>
ADMIN_USER=<admin-username>
ADMIN_PASSWORD_HASH=<Werkzeug-hash>
COOKIE_SECURE=1
REQUIRE_HTTPS=1
TRUST_PROXY=1
SITE_NAME=University Connect
```

Example Gunicorn command:

```bash
gunicorn --workers 3 --bind 127.0.0.1:8000 app:app
```

Keep `.env`, `database.db`, uploaded media and private media out of Git. Terminate TLS at Nginx with a valid certificate and enable HSTS.

## Commercial roadmap foundation

The schema now contains the building blocks for:

- universities and university-specific data
- SUPER_ADMIN / UNIVERSITY_ADMIN / DEPARTMENT_ADMIN / MODERATOR / TEACHER / STUDENT / ALUMNI roles
- announcements
- clubs and club membership
- events and registrations
- notifications
- subscriptions

The existing application still uses SQLite and the current profile fields, so the next major architectural migration is PostgreSQL + strict tenant isolation. That migration should be done as a deliberate database migration rather than silently replacing the working MVP. Billing also requires a payment provider and credentials, so the subscription table is only the data foundation at this stage.

## Security

This project is not “unhackable”. Production security also depends on TLS, host configuration, dependency updates, backups, database permissions, monitoring, malware scanning, rate limiting and infrastructure security.

## Public entry point and admin access

- `/` is the public user portal entry point. Visitors are sent to the user login page; authenticated users go directly to their user home.
- The admin dashboard is at `/admin` and is protected by the admin session.
- The public user interface does not display an admin-login link. Administrators must navigate directly to `/login`.
- `/login` is the administrator authentication page; successful authentication opens `/admin`.

## Messaging and campus services update

### Messaging
- Chat sending now includes CSRF correctly.
- Chat supports AJAX sending without a page reload.
- Open chats poll `/api/messages/<user_id>` every 2 seconds for new messages.
- New messages are appended live and the conversation auto-scrolls.
- Incoming messages are marked read while the conversation is open.

### Campus services
The earlier database tables were only the foundation; they did not constitute finished user-facing services. This build adds working MVP screens/routes for:
- Announcements
- Clubs with join/leave
- Events with register/unregister
- Notifications with mark-as-read and JSON endpoint
- Admin campus management at `/admin/campus`

### Still foundation-only
- Subscription/billing is not a payment system yet.
- Multi-university tables/roles exist as a foundation, but full tenant isolation and role-management UI are not complete.
- Real-time messaging uses lightweight polling rather than WebSockets, so it works with ordinary WSGI hosting without requiring a separate socket service.
