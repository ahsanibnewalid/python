import os
from datetime import datetime
from functools import wraps
from flask import request, session, redirect, url_for, render_template, flash, abort

SCOPES = {
    "users": "Users & accounts",
    "content": "Posts, Stories & Reels",
    "reports": "Reports & safety",
    "universities": "Universities & academics",
    "companies": "Companies & jobs",
    "campus": "Campus services",
    "resources": "Study resources",
    "analytics": "Analytics & insights",
}


def install(app, get_db_connection, init_db):
    def ensure_schema():
        conn = get_db_connection()
        conn.execute("""CREATE TABLE IF NOT EXISTS admin_staff (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL DEFAULT 'moderator',
            status TEXT NOT NULL DEFAULT 'active',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS moderator_scopes (
            staff_id INTEGER NOT NULL,
            scope TEXT NOT NULL,
            PRIMARY KEY(staff_id, scope),
            FOREIGN KEY(staff_id) REFERENCES admin_staff(id) ON DELETE CASCADE
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS moderation_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER,
            action TEXT NOT NULL,
            scope TEXT NOT NULL,
            target_type TEXT DEFAULT '',
            target_id TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(staff_id) REFERENCES admin_staff(id) ON DELETE SET NULL
        )""")
        conn.commit()
        conn.close()

    def owner_required(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get("logged_in") or not session.get("admin_username"):
                return redirect(url_for("login"))
            ensure_schema()
            owner = os.environ.get("ADMIN_USER", "admin")
            if session.get("admin_username") != owner:
                return abort(403)
            return fn(*args, **kwargs)
        return wrapped

    def staff_identity():
        if not session.get("logged_in"):
            return None
        ensure_schema()
        username = session.get("admin_username")
        if username:
            return None
        user_id = session.get("user_id")
        if not user_id:
            return None
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM admin_staff WHERE user_id=? AND status='active'", (user_id,)).fetchone()
        conn.close()
        return row

    @app.before_request
    def route_legacy_admin_to_god():
        if request.path == "/admin" and request.method == "GET":
            return redirect(url_for("admin_god"))

    @app.route("/admin/god", methods=["GET"])
    @owner_required
    def admin_god():
        ensure_schema()
        conn = get_db_connection()
        counts = {}
        for key, table in [("users","users"),("posts","posts"),("reels","reels"),("stories","stories"),("reports","message_reports"),("jobs","platform_jobs")]:
            try:
                counts[key] = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            except Exception:
                counts[key] = 0
        staff = conn.execute("SELECT * FROM admin_staff ORDER BY id DESC").fetchall()
        staff_data = []
        for s in staff:
            scopes = conn.execute("SELECT scope FROM moderator_scopes WHERE staff_id=? ORDER BY scope", (s["id"],)).fetchall()
            staff_data.append({"id":s["id"],"username":s["username"],"role":s["role"],"status":s["status"],"created_by":s["created_by"],"created_at":s["created_at"],"scopes":[x["scope"] for x in scopes]})
        recent = conn.execute("SELECT id, action, scope, target_type, target_id, note, created_at FROM moderation_actions ORDER BY id DESC LIMIT 30").fetchall()
        conn.close()
        return render_template("admin_god.html", counts=counts, staff=staff_data, scopes=SCOPES, actions=recent, owner=os.environ.get("ADMIN_USER","admin"))

    @app.route("/admin/god/staff/add", methods=["POST"])
    @owner_required
    def admin_god_add_staff():
        ensure_schema()
        username = request.form.get("username", "").strip()
        selected = [s for s in request.form.getlist("scope") if s in SCOPES]
        if not username or not selected:
            flash("Choose a user and at least one moderation responsibility.", "error")
            return redirect(url_for("admin_god"))
        conn = get_db_connection()
        user = conn.execute("SELECT id, username FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
        if not user:
            conn.close(); flash("That user account does not exist.", "error"); return redirect(url_for("admin_god"))
        try:
            now = datetime.utcnow().isoformat()
            cur = conn.execute("INSERT INTO admin_staff(user_id,username,role,status,created_by,created_at) VALUES(?,?,?,?,?,?)", (user["id"], user["username"], "moderator", "active", session.get("admin_username"), now))
            staff_id = cur.lastrowid
            for scope in selected:
                conn.execute("INSERT INTO moderator_scopes(staff_id,scope) VALUES(?,?)", (staff_id, scope))
            conn.commit()
            flash(f"{user['username']} is now an active moderator.", "success")
        except Exception as exc:
            conn.rollback(); flash("Could not assign moderator: account may already be assigned.", "error")
        finally:
            conn.close()
        return redirect(url_for("admin_god"))

    @app.route("/admin/god/staff/<int:staff_id>/scopes", methods=["POST"])
    @owner_required
    def admin_god_scopes(staff_id):
        selected = [s for s in request.form.getlist("scope") if s in SCOPES]
        conn = get_db_connection()
        conn.execute("DELETE FROM moderator_scopes WHERE staff_id=?", (staff_id,))
        for scope in selected:
            conn.execute("INSERT INTO moderator_scopes(staff_id,scope) VALUES(?,?)", (staff_id, scope))
        conn.commit(); conn.close()
        flash("Moderator responsibilities updated.", "success")
        return redirect(url_for("admin_god"))

    @app.route("/admin/god/staff/<int:staff_id>/toggle", methods=["POST"])
    @owner_required
    def admin_god_toggle_staff(staff_id):
        conn = get_db_connection()
        row = conn.execute("SELECT status FROM admin_staff WHERE id=?", (staff_id,)).fetchone()
        if row:
            status = "suspended" if row["status"] == "active" else "active"
            conn.execute("UPDATE admin_staff SET status=? WHERE id=?", (status, staff_id)); conn.commit()
        conn.close(); return redirect(url_for("admin_god"))

    @app.route("/moderator", methods=["GET"])
    def moderator_dashboard():
        staff = staff_identity()
        if not staff:
            return abort(403)
        conn = get_db_connection()
        scopes = [x["scope"] for x in conn.execute("SELECT scope FROM moderator_scopes WHERE staff_id=?", (staff["id"],)).fetchall()]
        actions = conn.execute("SELECT * FROM moderation_actions WHERE staff_id=? ORDER BY id DESC LIMIT 20", (staff["id"],)).fetchall()
        conn.close()
        return render_template("moderator_dashboard.html", staff=staff, scopes=scopes, scope_labels=SCOPES, actions=actions)

    return True
