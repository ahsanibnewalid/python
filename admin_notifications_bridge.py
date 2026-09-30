"""Persist System Owner moderator events into the user's notification center."""
from datetime import datetime, timezone
from flask import request


def _now():
    return datetime.now(timezone.utc).isoformat()


def install(app, get_db_connection):
    if app.extensions.get("uc_admin_notification_bridge"):
        return
    app.extensions["uc_admin_notification_bridge"] = True

    def ensure_table(conn):
        conn.execute("""CREATE TABLE IF NOT EXISTS platform_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'system',
            title TEXT NOT NULL,
            body TEXT DEFAULT '',
            url TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )""")

    @app.after_request
    def persist_admin_notification(response):
        if request.method != "POST" or response.status_code not in (301, 302, 303, 307, 308):
            return response
        endpoint = request.endpoint or ""
        if endpoint not in {
            "admin_god_add_staff",
            "admin_god_add_work",
            "admin_god_scopes",
            "admin_god_toggle_staff",
        }:
            return response

        try:
            conn = get_db_connection()
            ensure_table(conn)
            user_id = None
            title = "University Connect admin update"
            body = ""

            if endpoint == "admin_god_add_staff":
                username = request.form.get("username", "").strip()
                row = conn.execute("SELECT id FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
                if row:
                    user_id = row["id"]
                    scopes = request.form.getlist("scope")
                    labels = ", ".join(scopes) if scopes else "assigned responsibilities"
                    title = "You are now a moderator"
                    body = f"The System Owner assigned you moderation responsibilities: {labels}."
            elif endpoint == "admin_god_add_work":
                staff_id = request.form.get("staff_id", type=int)
                row = conn.execute("SELECT user_id,username FROM admin_staff WHERE id=?", (staff_id,)).fetchone()
                if row:
                    user_id = row["user_id"]
                    title = "New moderation work assigned"
                    body = request.form.get("title", "You have new moderation work.").strip()
            elif endpoint == "admin_god_scopes":
                staff_id = request.view_args.get("staff_id")
                row = conn.execute("SELECT user_id FROM admin_staff WHERE id=?", (staff_id,)).fetchone()
                if row:
                    user_id = row["user_id"]
                    title = "Moderator responsibilities updated"
                    body = "Your moderation responsibilities were updated by the System Owner."
            elif endpoint == "admin_god_toggle_staff":
                staff_id = request.view_args.get("staff_id")
                row = conn.execute("SELECT user_id,status FROM admin_staff WHERE id=?", (staff_id,)).fetchone()
                if row:
                    user_id = row["user_id"]
                    title = "Moderator status updated"
                    body = "Your moderator access has been updated by the System Owner."

            if user_id:
                conn.execute(
                    "INSERT INTO platform_notifications(user_id,kind,title,body,url,created_at) VALUES(?,?,?,?,?,?)",
                    (int(user_id), "moderation", title, body, "/moderator", _now()),
                )
                conn.commit()
            else:
                conn.rollback()
            conn.close()
        except Exception:
            # Notification delivery must never break a successful admin action.
            try:
                conn.close()
            except Exception:
                pass
        return response

    return True
