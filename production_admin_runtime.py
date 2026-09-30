"""Render production bootstrap for persistent administrator credentials.

The application historically read ADMIN_USER/ADMIN_PASSWORD_HASH from the
process environment at import time. This wrapper keeps that compatibility while
making the actual administrator credential authoritative in PostgreSQL.

On first deployment, Render supplies ADMIN_BOOTSTRAP_TOKEN. The first admin
uses /admin/first-setup to choose the username/password. The bootstrap token
should then be removed from Render. Password changes are made from the admin
panel and persist in PostgreSQL across deploys/restarts/workers.
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


def prepare_environment():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        # Local/non-Postgres development retains the existing behavior.
        if not os.environ.get("ADMIN_PASSWORD_HASH"):
            os.environ["ADMIN_PASSWORD_HASH"] = generate_password_hash(
                secrets.token_urlsafe(32)
            )
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
            # If no hash was supplied, create an unknown random bootstrap
            # password. The first-setup token is the only supported way to set
            # a known password.
            if not password_hash:
                password_hash = generate_password_hash(secrets.token_urlsafe(48))
            conn.execute(
                "INSERT INTO admin_credentials(id,username,password_hash,setup_completed,created_at,updated_at) VALUES(1,%s,%s,0,%s,%s)",
                (username, password_hash, now(), now()),
            )
            conn.commit()

    # app.py requires this variable during production import. The before-request
    # hook in admin_runtime.py will refresh it from PostgreSQL on every request.
    os.environ["ADMIN_USER"] = username
    os.environ["ADMIN_PASSWORD_HASH"] = password_hash


def main():
    prepare_environment()
    import subprocess

    port = os.environ.get("PORT", "10000")
    cmd = [
        sys.executable,
        "-m",
        "gunicorn",
        "--workers",
        os.environ.get("WEB_CONCURRENCY", "2"),
        "--threads",
        "4",
        "--timeout",
        "120",
        "--bind",
        f"0.0.0.0:{port}",
        "--config",
        "gunicorn_admin.conf.py",
        "app:app",
    ]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
