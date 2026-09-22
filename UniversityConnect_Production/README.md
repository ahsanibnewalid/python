# University Connect — Production-Ready Flask Package

A Flask-based university social/community site with student profiles, CV fields, newsfeed posts, comments, likes, messaging, galleries, admin controls, and configurable site branding.

## Included

- `app.py` — main Flask application
- `templates/` — all HTML templates from the supplied project
- `requirements.txt` — runtime dependencies
- `.env.example` — configuration template
- `setup_local.py` — creates a local `.env` and securely hashes the admin password
- Windows and Linux setup/start scripts
- `start_production.sh` — Gunicorn production example
- `nginx_university_site.conf.example` — HTTPS reverse-proxy example
- `static/uploads/` and `private_media/posts/` directories

## Run locally

### Windows

1. Run `install_windows.bat`.
2. Choose an admin password when prompted.
3. Run `start_windows.bat`.
4. Open `http://127.0.0.1:5000/user-login`.
5. Admin login: `http://127.0.0.1:5000/login`.

### Linux/macOS

```bash
./install_linux.sh
./start_linux.sh
```

## Production deployment

Use a real WSGI server such as Gunicorn behind Nginx. Flask's built-in development server is intended for development, not production. See the official Flask deployment guidance. 

Before starting production, set:

```text
APP_ENV=production
FLASK_SECRET_KEY=<long random secret>
ADMIN_USER=<admin username>
ADMIN_PASSWORD_HASH=<Werkzeug password hash>
COOKIE_SECURE=1
REQUIRE_HTTPS=1
TRUST_PROXY=1
SITE_NAME=University Connect
```

Example:

```bash
gunicorn --workers 3 --bind 127.0.0.1:8000 app:app
```

Terminate TLS at Nginx using a valid certificate and redirect HTTP to HTTPS. Do not expose `private_media/` as a public static directory.

## Important security notes

- The production app refuses to start without `FLASK_SECRET_KEY` and `ADMIN_PASSWORD_HASH`.
- The old hard-coded admin password fallback has been removed.
- Debug mode is disabled in `app.py`.
- CSRF checks are present on state-changing routes.
- Sessions use HttpOnly/SameSite settings, with Secure enabled when `COOKIE_SECURE=1`.
- Keep `.env`, `database.db`, and uploaded/private media out of public repositories.
- Back up `database.db` and uploaded media before upgrades.
- For larger deployments, migrate from SQLite to PostgreSQL and use object storage for media.

## Database

The application creates/migrates `database.db` automatically on startup. This package intentionally does not include a database containing real users.

## HTTPS / MITM protection

Application code alone cannot make a site “unhackable” or completely MITM-proof. Correct TLS configuration is required. Use Nginx (or another trusted reverse proxy), a valid certificate, HTTPS redirects, HSTS, `COOKIE_SECURE=1`, and `REQUIRE_HTTPS=1`.

Official Flask documentation:
https://flask.palletsprojects.com/en/stable/deploying/
