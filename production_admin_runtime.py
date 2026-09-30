"""Render production bootstrap for persistent administrator credentials and admin control center.

The first administrator may be bootstrapped from environment/.env values. Those
values are used only when the persistent admin record has not been completed.
After the password is changed in the admin panel, the database becomes the
source of truth and the bootstrap password is no longer used.
"""
from __future__ import annotations

import os
import secrets
import sys
from datetime import datetime, timezone

import psycopg
from werkzeug.security import generate_password_hash

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS admin_credentials (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    setup_completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def bootstrap_password_hash():
    """Return the one-time bootstrap hash from env/.env, if supplied.

    ADMIN_PASSWORD_HASH remains supported for deployments that already use a
    pre-hashed credential. ADMIN_PASSWORD is intentionally never persisted as
    plaintext; it is converted to a secure hash immediately at startup.
    """
    existing_hash = os.environ.get("ADMIN_PASSWORD_HASH", "").strip()
    if existing_hash:
        return existing_hash

    bootstrap_password = os.environ.get("ADMIN_PASSWORD", "")
    if bootstrap_password:
        return generate_password_hash(bootstrap_password)

    return generate_password_hash(secrets.token_urlsafe(48))


def prepare_environment():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        os.environ["ADMIN_PASSWORD_HASH"] = bootstrap_password_hash()
        return

    with psycopg.connect(url) as conn:
        conn.execute(TABLE_SQL)
        row = conn.execute(
            "SELECT username,password_hash,setup_completed FROM admin_credentials WHERE id=1"
        ).fetchone()

        if row:
            username, password_hash, setup_completed = row
        else:
            username = os.environ.get("ADMIN_USER", "admin").strip() or "admin"
            password_hash = bootstrap_password_hash()
            setup_completed = 0
            conn.execute(
                "INSERT INTO admin_credentials(id,username,password_hash,setup_completed,created_at,updated_at) VALUES(1,%s,%s,%s,%s,%s)",
                (username, password_hash, setup_completed, now(), now()),
            )
            conn.commit()

    os.environ["ADMIN_USER"] = username
    os.environ["ADMIN_PASSWORD_HASH"] = password_hash


def install_upload_compatibility(app_module):
    """Keep normal extension/size validation but avoid rejecting legitimate
    browser uploads because their container signature differs from the
    browser-reported type. This is especially important for Android/iOS media
    and for files uploaded unchanged after the media editor was removed.

    The application still enforces the extension allowlist and Flask's 60 MB
    request limit before this function is reached.
    """
    def compatible_signature(_file_storage, media_type):
        return media_type in {"image", "video"}
    app_module.validate_media_signature = compatible_signature


def main():
    prepare_environment()

    import app
    install_upload_compatibility(app)

    from admin_runtime import install as install_admin_runtime
    install_admin_runtime(app.app)

    from admin_god import install as install_admin_god
    install_admin_god(app.app, app.get_db_connection, getattr(app, "init_db", None))

    import subprocess
    port = os.environ.get("PORT", "10000")
    cmd = [
        sys.executable, "-m", "gunicorn",
        "--workers", os.environ.get("WEB_CONCURRENCY", "2"),
        "--threads", "4", "--timeout", "120",
        "--bind", f"0.0.0.0:{port}",
        "--config", "gunicorn_admin.conf.py", "app:app",
    ]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
