# Render administrator setup

University Connect now stores the production administrator username and password hash in PostgreSQL. Render environment variables are used only to bootstrap the first administrator.

## First deployment

1. Deploy the `render.yaml` blueprint so the web service and PostgreSQL database exist.
2. In Render → Environment, create `ADMIN_BOOTSTRAP_TOKEN` as a long random secret. Do not commit it to GitHub.
3. Keep `APP_ENV=production`, `DATABASE_URL`, `FLASK_SECRET_KEY`, `TRUST_PROXY=1`, `REQUIRE_HTTPS=1`, and `COOKIE_SECURE=1` configured.
4. Redeploy the service.
5. Open `/admin/first-setup` on the deployed website.
6. Enter the deployment token, choose the administrator username, and create a password of at least 12 characters.
7. After successful setup, remove `ADMIN_BOOTSTRAP_TOKEN` from Render and redeploy.

The bootstrap token is not stored in PostgreSQL, a cookie, a session, or the administrator record. It is compared in constant time and is only accepted while the database administrator record is marked as not yet set up.

## Changing the administrator password

After signing in, open `/admin/password`. Enter the current password and choose a new username/password. The application writes only a Werkzeug password hash to PostgreSQL and signs the administrator session out after the change.

The credential row is refreshed from PostgreSQL before requests in each Gunicorn worker, so a password change does not require a code deployment and propagates across workers.

## Legacy compatibility

`ADMIN_USER` and `ADMIN_PASSWORD_HASH` remain supported for existing deployments. When no administrator row exists, the production launcher seeds the row from those values if present. Once the first setup is completed, PostgreSQL becomes authoritative.

Never put a plaintext administrator password in GitHub, `render.yaml`, source code, or application logs.
