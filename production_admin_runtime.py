"""Render production bootstrap for persistent administrator credentials and admin control center."""
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


def prepare_environment():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        if not os.environ.get("ADMIN_PASSWORD_HASH"):
            os.environ["ADMIN_PASSWORD_HASH"] = generate_password_hash(secrets.token_urlsafe(32))
        return

    with psycopg.connect(url) as conn:
        conn.execute(TABLE_SQL)
        row = conn.execute(
            "SELECT username,password_hash,setup_completed FROM admin_credentials WHERE id=1"
        ).fetchone()
        if row:
            username, password_hash, _ = row
        else:
            username = os.environ.get("ADMIN_USER", "admin").strip() or "admin"
            password_hash = os.environ.get("ADMIN_PASSWORD_HASH", "")
            if not password_hash:
                password_hash = generate_password_hash(secrets.token_urlsafe(48))
            conn.execute(
                "INSERT INTO admin_credentials(id,username,password_hash,setup_completed,created_at,updated_at) VALUES(1,%s,%s,0,%s,%s)",
                (username, password_hash, now(), now()),
            )
            conn.commit()

    os.environ["ADMIN_USER"] = username
    os.environ["ADMIN_PASSWORD_HASH"] = password_hash


def main():
    prepare_environment()

    # Install the new system-owner command center before Gunicorn loads the
    # application workers. This keeps the legacy admin URLs intact for
    # compatibility while making /admin the single modern control entrypoint.
    import app
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
