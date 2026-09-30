"""Persistent administrator credential routes for the Render deployment."""
from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone

from flask import flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


def _now():
    return datetime.now(timezone.utc).isoformat()


def install(flask_app):
    # Gunicorn loads this once per worker. The credential values are refreshed
    # from PostgreSQL before every request so password changes propagate to all
    # workers without a restart.
    if flask_app.extensions.get("uc_admin_runtime_installed"):
        return
    flask_app.extensions["uc_admin_runtime_installed"] = True

    import app as app_module
    get_db_connection = app_module.get_db_connection
    require_csrf = app_module.require_csrf

    def load_credentials():
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT username,password_hash,setup_completed FROM admin_credentials WHERE id=1"
            ).fetchone()
            if row:
                app_module.ADMIN_USER = row["username"]
                app_module.ADMIN_PASSWORD_HASH = row["password_hash"]
            return row
        finally:
            conn.close()

    @flask_app.before_request
    def _sync_admin_credentials():
        try:
            load_credentials()
        except Exception:
            # Keep the last known credentials if the DB is temporarily
            # unavailable. Normal application health/error handling remains in
            # charge of the request itself.
            pass

    def admin_session_required():
        return bool(session.get("logged_in") and session.get("admin_username"))

    @flask_app.route("/admin/first-setup", methods=["GET", "POST"])
    def admin_first_setup():
        row = load_credentials()
        if row and int(row["setup_completed"] or 0) == 1:
            return redirect(url_for("login"))

        bootstrap_token = os.environ.get("ADMIN_BOOTSTRAP_TOKEN", "").strip()
        if not bootstrap_token:
            return (
                "Administrator first setup is disabled. Set ADMIN_BOOTSTRAP_TOKEN "
                "in Render and redeploy, then open this page.",
                503,
            )

        if request.method == "POST":
            require_csrf()
            supplied = request.form.get("bootstrap_token", "")
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")

            if not hmac.compare_digest(supplied, bootstrap_token):
                flash("Invalid deployment setup token.", "error")
            elif not 3 <= len(username) <= 40 or not all(c.isalnum() or c in "_.-" for c in username):
                flash("Username must be 3–40 characters and use letters, numbers, dot, underscore or hyphen.", "error")
            elif len(password) < 12:
                flash("Use a password with at least 12 characters.", "error")
            elif password != confirm:
                flash("The password confirmation does not match.", "error")
            else:
                conn = get_db_connection()
                try:
                    conn.execute(
                        "UPDATE admin_credentials SET username=?, password_hash=?, setup_completed=1, updated_at=? WHERE id=1",
                        (username, generate_password_hash(password), _now()),
                    )
                    conn.commit()
                    app_module.ADMIN_USER = username
                    # Do not leave the bootstrap token in a session or response.
                    flash("Administrator account created. Remove ADMIN_BOOTSTRAP_TOKEN from Render now, then sign in.", "success")
                    return redirect(url_for("login"))
                finally:
                    conn.close()

        return render_template("admin_first_setup.html", csrf=app_module.csrf_token())

    @flask_app.route("/admin/password", methods=["GET", "POST"])
    def admin_password():
        if not admin_session_required():
            return redirect(url_for("login", next="/admin/password"))
        row = load_credentials()
        if not row:
            flash("Administrator credentials are not initialized.", "error")
            return redirect(url_for("admin_first_setup"))

        if request.method == "POST":
            require_csrf()
            current = request.form.get("current_password", "")
            username = request.form.get("username", "").strip()
            password = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")

            if not check_password_hash(row["password_hash"], current):
                flash("Current administrator password is incorrect.", "error")
            elif not 3 <= len(username) <= 40 or not all(c.isalnum() or c in "_.-" for c in username):
                flash("Username must be 3–40 characters and use letters, numbers, dot, underscore or hyphen.", "error")
            elif len(password) < 12:
                flash("Use a password with at least 12 characters.", "error")
            elif password != confirm:
                flash("The password confirmation does not match.", "error")
            else:
                conn = get_db_connection()
                try:
                    conn.execute(
                        "UPDATE admin_credentials SET username=?, password_hash=?, setup_completed=1, updated_at=? WHERE id=1",
                        (username, generate_password_hash(password), _now()),
                    )
                    conn.commit()
                finally:
                    conn.close()
                app_module.ADMIN_USER = username
                app_module.ADMIN_PASSWORD_HASH = generate_password_hash(password)
                session.pop("logged_in", None)
                session.pop("admin_username", None)
                flash("Administrator credentials changed. Please sign in again.", "success")
                return redirect(url_for("login"))

        return render_template(
            "admin_password.html",
            current_username=row["username"],
            csrf=app_module.csrf_token(),
        )
