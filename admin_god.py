import os
import hmac
import io
import json
import tempfile
import urllib.error
import urllib.request
import urllib.parse
import zipfile
from datetime import datetime
from functools import wraps
from flask import request, session, redirect, url_for, render_template, flash, abort, jsonify, send_file

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
        conn.execute("""CREATE TABLE IF NOT EXISTS moderator_work (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER NOT NULL,
            scope TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT DEFAULT NULL,
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
        conn.execute("""CREATE TABLE IF NOT EXISTS platform_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'system',
            title TEXT NOT NULL,
            body TEXT DEFAULT '',
            url TEXT DEFAULT '',
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )""")
        # Repair legacy duplicate moderator rows before enforcing the user_id uniqueness rule.
        # Keep the oldest assignment row; its dependent scopes/work cascade on deletion.
        conn.execute("""
            DELETE FROM admin_staff
            WHERE user_id IS NOT NULL
              AND id NOT IN (
                  SELECT MIN(id) FROM admin_staff
                  WHERE user_id IS NOT NULL
                  GROUP BY user_id
              )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_admin_staff_user ON admin_staff(user_id) WHERE user_id IS NOT NULL")
        conn.execute("""CREATE TABLE IF NOT EXISTS mobile_builds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL UNIQUE,
            run_id INTEGER,
            status TEXT NOT NULL DEFAULT 'queued',
            conclusion TEXT DEFAULT NULL,
            artifact_id INTEGER DEFAULT NULL,
            artifact_name TEXT DEFAULT 'university-connect-android-debug',
            requested_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            error TEXT DEFAULT ''
        )""")
        conn.commit(); conn.close()

    def notify_staff(conn, user_id, title, body):
        if user_id:
            conn.execute(
                "INSERT INTO platform_notifications(user_id,kind,title,body,url,is_read,created_at) VALUES(?,?,?,?,?,?,?)",
                (int(user_id), "moderation", title, body, "/moderator", 0, datetime.utcnow().isoformat()),
            )

    def csrf_ok():
        expected = str(session.get("csrf_token", ""))
        supplied = request.form.get("csrf_token", "")
        return bool(expected and supplied and hmac.compare_digest(expected, supplied))

    def owner_required(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get("logged_in") or not session.get("admin_username"):
                return redirect(url_for("login"))
            ensure_schema()
            import app as app_module
            owner = getattr(app_module, "ADMIN_USER", os.environ.get("ADMIN_USER", "admin"))
            if session.get("admin_username") != owner:
                return abort(403)
            if request.method == "POST" and not csrf_ok():
                return abort(400, description="CSRF validation failed.")
            return fn(*args, **kwargs)
        return wrapped

    def staff_identity():
        if not session.get("user_logged_in") or session.get("user_id") is None:
            return None
        ensure_schema()
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM admin_staff WHERE user_id=? AND status='active'", (int(session["user_id"]),)).fetchone()
        conn.close(); return row

    @app.before_request
    def route_legacy_admin_to_god():
        if request.path == "/admin" and request.method == "GET":
            return redirect(url_for("admin_god"))
        if request.path.startswith("/admin/") and not request.path.startswith(("/admin/god", "/admin/first-setup", "/admin/password")):
            return redirect(url_for("admin_god"))

    @app.route("/admin/god", methods=["GET"])
    @owner_required
    def admin_god():
        ensure_schema(); conn = get_db_connection(); counts = {}
        for key, table in [("users","users"),("posts","posts"),("reels","reels"),("stories","stories"),("reports","message_reports"),("jobs","platform_jobs")]:
            try: counts[key] = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            except Exception: counts[key] = 0
        staff = conn.execute("SELECT * FROM admin_staff ORDER BY id DESC").fetchall(); staff_data=[]
        import app as app_module
        owner_username = getattr(app_module, "ADMIN_USER", os.environ.get("ADMIN_USER", "admin")).strip()
        for s in staff:
            scopes=conn.execute("SELECT scope FROM moderator_scopes WHERE staff_id=? ORDER BY scope",(s["id"],)).fetchall()
            work=conn.execute("SELECT COUNT(*) AS c FROM moderator_work WHERE staff_id=? AND status='open'",(s["id"],)).fetchone()["c"]
            is_owner = bool(s["username"]) and s["username"].casefold() == owner_username.casefold()
            staff_data.append({"id":s["id"],"username":s["username"],"role":s["role"],"status":s["status"],"created_by":s["created_by"],"created_at":s["created_at"],"scopes":[x["scope"] for x in scopes],"open_work":work,"is_owner":is_owner})
        users=conn.execute("SELECT username FROM users WHERE username IS NOT NULL AND username!='' ORDER BY username LIMIT 500").fetchall()
        recent=conn.execute("SELECT id,action,scope,target_type,target_id,note,created_at FROM moderation_actions ORDER BY id DESC LIMIT 30").fetchall()
        try:
            trust_spaces=conn.execute("""
                SELECT s.id,s.slug,s.display_name,s.kind,s.verification_status,
                       COALESCE(i.name,o.name) owner_space_name
                FROM platform_spaces s
                LEFT JOIN institutions i ON i.id=s.institution_id
                LEFT JOIN organizations o ON o.id=s.organization_id
                WHERE s.verification_status!='verified'
                ORDER BY s.created_at DESC LIMIT 100
            """).fetchall()
        except Exception:
            trust_spaces=[]
        conn.close()
        provider=os.environ.get("MEDIA_STORAGE","local").strip().lower()
        storage_ready = provider in {"s3","r2","b2"} and all(
            os.environ.get(k,"").strip()
            for k in ("MEDIA_S3_BUCKET","MEDIA_S3_ACCESS_KEY","MEDIA_S3_SECRET_KEY")
        )
        if provider == "local" and os.environ.get("APP_ENV","development") == "production":
            storage_status="warning"
            storage_label="Local media storage is ephemeral"
        elif provider in {"s3","r2","b2"} and storage_ready:
            storage_status="ready"
            storage_label=f"{provider.upper()} object storage configured"
        else:
            storage_status="warning"
            storage_label="Object storage credentials are incomplete"
        health={
            "database":"ready",
            "media_status":storage_status,
            "media_label":storage_label,
            "secret_status":"ready" if os.environ.get("FLASK_SECRET_KEY","").strip() else "warning",
            "environment":os.environ.get("APP_ENV","development"),
        }
        mobile_build=refresh_mobile_build(mobile_build_row())
        return render_template("admin_god.html",counts=counts,staff=staff_data,scopes=SCOPES,actions=recent,users=users,trust_spaces=trust_spaces,health=health,owner=getattr(app_module,"ADMIN_USER",os.environ.get("ADMIN_USER","admin")),csrf=session.get("csrf_token",""),mobile_build=mobile_build,mobile_builder_configured=bool(github_config()[0]))

    def github_config():
        token = os.environ.get("GITHUB_ACTIONS_TOKEN", "").strip()
        repo = os.environ.get("GITHUB_ACTIONS_REPO", "ahsanibnewalid/python").strip()
        workflow = os.environ.get("GITHUB_APK_WORKFLOW", "build-android-apk.yml").strip()
        return token, repo, workflow

    def github_json(method, path, payload=None):
        token, repo, _ = github_config()
        if not token:
            raise RuntimeError("GITHUB_ACTIONS_TOKEN is not configured on the server.")
        req = urllib.request.Request(
            "https://api.github.com" + path,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + token,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "University-Connect-Admin",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                raw = response.read()
                return response.status, json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError("GitHub API error %s: %s" % (exc.code, detail[:500]))

    def mobile_build_row():
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM mobile_builds ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        return row

    def refresh_mobile_build(row):
        if not row or not row["run_id"] or row["status"] in ("success", "failure", "cancelled"):
            return row
        token, repo, workflow = github_config()
        if not token:
            return row
        try:
            _, run = github_json("GET", "/repos/%s/actions/runs/%s" % (repo, row["run_id"]))
            status = run.get("status") or "queued"
            conclusion = run.get("conclusion")
            if status == "completed":
                state = "success" if conclusion == "success" else ("cancelled" if conclusion == "cancelled" else "failure")
            else:
                state = status
            artifact_id = row["artifact_id"]
            artifact_name = row["artifact_name"]
            if state == "success":
                _, data = github_json("GET", "/repos/%s/actions/runs/%s/artifacts?name=%s&per_page=10" % (
                    repo, row["run_id"], urllib.parse.quote(artifact_name, safe="")
                ))
                artifacts = data.get("artifacts") or []
                if artifacts:
                    artifact_id = artifacts[0].get("id")
                    artifact_name = artifacts[0].get("name") or artifact_name
            conn = get_db_connection()
            conn.execute(
                "UPDATE mobile_builds SET status=?,conclusion=?,artifact_id=?,artifact_name=?,updated_at=?,error=? WHERE id=?",
                (state, conclusion, artifact_id, artifact_name, datetime.utcnow().isoformat(), "", row["id"]),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM mobile_builds WHERE id=?", (row["id"],)).fetchone()
            conn.close()
        except Exception as exc:
            conn = get_db_connection()
            conn.execute("UPDATE mobile_builds SET error=?,updated_at=? WHERE id=?", (str(exc)[:1000], datetime.utcnow().isoformat(), row["id"]))
            conn.commit()
            row = conn.execute("SELECT * FROM mobile_builds WHERE id=?", (row["id"],)).fetchone()
            conn.close()
        return row


    @app.route("/admin/god/github/test", methods=["GET"])
    @owner_required
    def admin_god_github_test():
        token, repo, workflow = github_config()
        if not token:
            return jsonify({
                "connected": False,
                "repository": repo,
                "workflow": workflow,
                "message": "GITHUB_ACTIONS_TOKEN is not configured on the server.",
            }), 503
        try:
            repo_status, repo_data = github_json("GET", "/repos/%s" % repo)
            workflow_status, workflow_data = github_json(
                "GET",
                "/repos/%s/actions/workflows/%s" % (repo, urllib.parse.quote(workflow, safe="")),
            )
            permissions = repo_data.get("permissions") or {}
            return jsonify({
                "connected": True,
                "repository": repo,
                "workflow": workflow,
                "repository_status": repo_status,
                "workflow_status": workflow_status,
                "repository_private": bool(repo_data.get("private")),
                "repository_default_branch": repo_data.get("default_branch"),
                "token_permissions": {
                    key: bool(permissions.get(key))
                    for key in ("admin", "maintain", "push", "triage", "pull")
                    if key in permissions
                },
                "workflow_name": workflow_data.get("name") or workflow,
                "workflow_state": workflow_data.get("state"),
                "message": "GitHub repository and APK workflow are reachable.",
            })
        except Exception as exc:
            return jsonify({
                "connected": False,
                "repository": repo,
                "workflow": workflow,
                "message": str(exc)[:700],
            }), 502

    @app.route("/admin/god/mobile/build", methods=["POST"])
    @owner_required
    def admin_god_mobile_build():
        token, repo, workflow = github_config()
        if not token:
            flash("APK builder is not connected. Set GITHUB_ACTIONS_TOKEN in the server environment.", "error")
            return redirect(url_for("admin_god"))
        import secrets
        request_id = secrets.token_hex(10)
        now = datetime.utcnow().isoformat()
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO mobile_builds(request_id,status,requested_by,created_at,updated_at) VALUES(?,?,?,?,?)",
            (request_id, "queued", session.get("admin_username") or "system-owner", now, now),
        )
        conn.commit()
        conn.close()
        try:
            github_json(
                "POST",
                "/repos/%s/actions/workflows/%s/dispatches" % (repo, workflow),
                {"ref": "main", "inputs": {"request_id": request_id}},
            )
            conn = get_db_connection()
            conn.execute("UPDATE mobile_builds SET status='dispatch_sent',updated_at=? WHERE request_id=?", (datetime.utcnow().isoformat(), request_id))
            conn.commit()
            conn.close()
            flash("Android APK build requested. This page will show the GitHub build status.", "success")
        except Exception as exc:
            conn = get_db_connection()
            conn.execute("UPDATE mobile_builds SET status='failure',error=?,updated_at=? WHERE request_id=?", (str(exc)[:1000], datetime.utcnow().isoformat(), request_id))
            conn.commit()
            conn.close()
            flash("Could not start the Android build: " + str(exc), "error")
        return redirect(url_for("admin_god"))

    @app.route("/admin/god/mobile/status", methods=["GET"])
    @owner_required
    def admin_god_mobile_status():
        row = mobile_build_row()
        if row and row["status"] == "dispatch_sent" and not row["run_id"]:
            token, repo, workflow = github_config()
            try:
                _, data = github_json("GET", "/repos/%s/actions/workflows/%s/runs?event=workflow_dispatch&per_page=10" % (repo, workflow))
                for run in data.get("workflow_runs") or []:
                    if row["request_id"] in (run.get("name") or ""):
                        conn = get_db_connection()
                        conn.execute("UPDATE mobile_builds SET run_id=?,status=?,updated_at=? WHERE id=?", (run.get("id"), run.get("status") or "queued", datetime.utcnow().isoformat(), row["id"]))
                        conn.commit()
                        conn.close()
                        row = mobile_build_row()
                        break
            except Exception as exc:
                if row:
                    return jsonify({"available": True, "status": row["status"], "error": str(exc)[:500]})
        row = refresh_mobile_build(row)
        if not row:
            return jsonify({"available": False})
        return jsonify({
            "available": True,
            "id": row["id"],
            "request_id": row["request_id"],
            "run_id": row["run_id"],
            "status": row["status"],
            "conclusion": row["conclusion"],
            "artifact_id": row["artifact_id"],
            "artifact_name": row["artifact_name"],
            "error": row["error"],
            "run_url": ("https://github.com/%s/actions/runs/%s" % (github_config()[1], row["run_id"])) if row["run_id"] else None,
            "download_url": url_for("admin_god_mobile_download", build_id=row["id"]) if row["artifact_id"] else None,
        })

    def download_github_apk(artifact_id, filename):
        token, repo, _ = github_config()
        if not token:
            return abort(503, description="APK builder is not configured.")
        req = urllib.request.Request(
            "https://api.github.com/repos/%s/actions/artifacts/%s/zip" % (repo, artifact_id),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + token,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "University-Connect-Admin",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                data = response.read()
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                apk_names = [n for n in archive.namelist() if n.lower().endswith(".apk")]
                if not apk_names:
                    return abort(502, description="The GitHub artifact does not contain an APK.")
                apk = archive.read(apk_names[0])
        except Exception as exc:
            app.logger.exception("APK artifact download failed")
            return abort(502, description="Could not retrieve the APK from GitHub: %s" % str(exc)[:300])
        return send_file(
            io.BytesIO(apk),
            mimetype="application/vnd.android.package-archive",
            as_attachment=True,
            download_name=filename,
        )

    @app.route("/admin/god/mobile/download/<int:build_id>", methods=["GET"])
    @owner_required
    def admin_god_mobile_download(build_id):
        row = mobile_build_row()
        if not row or int(row["id"]) != int(build_id):
            return abort(404)
        row = refresh_mobile_build(row)
        if row["status"] != "success" or not row["artifact_id"]:
            return abort(404, description="A completed APK artifact is not available yet.")
        return download_github_apk(
            row["artifact_id"],
            "university-connect-debug-%s.apk" % row["id"],
        )

    @app.route("/admin/god/mobile/download-latest", methods=["GET"])
    @owner_required
    def admin_god_mobile_download_latest():
        token, repo, workflow = github_config()
        if not token:
            return abort(503, description="APK builder is not configured.")
        try:
            _, data = github_json(
                "GET",
                "/repos/%s/actions/workflows/%s/runs?branch=main&status=success&per_page=20"
                % (repo, urllib.parse.quote(workflow, safe="")),
            )
            runs = data.get("workflow_runs") or []
            for run in runs:
                run_id = run.get("id")
                if not run_id:
                    continue
                _, artifacts_data = github_json(
                    "GET",
                    "/repos/%s/actions/runs/%s/artifacts?per_page=50" % (repo, run_id),
                )
                for artifact in artifacts_data.get("artifacts") or []:
                    name = (artifact.get("name") or "").lower()
                    if artifact.get("expired"):
                        continue
                    if "apk" in name or name == "university-connect-android-debug":
                        return download_github_apk(
                            artifact.get("id"),
                            "university-connect-latest.apk",
                        )
            return abort(404, description="No non-expired successful APK artifact was found. Build the APK first.")
        except Exception as exc:
            app.logger.exception("Latest APK lookup failed")
            return abort(502, description="Could not find the latest APK on GitHub: %s" % str(exc)[:300])

    @app.route("/admin/god/space/<int:space_id>/verify", methods=["POST"])
    @owner_required
    def admin_god_verify_space(space_id):
        conn=get_db_connection()
        try:
            space=conn.execute("SELECT * FROM platform_spaces WHERE id=?",(space_id,)).fetchone()
            if not space:
                conn.close(); abort(404)
            owner_user_id=None
            try:
                owner_row=conn.execute("SELECT id FROM users WHERE lower(username)=lower(?) LIMIT 1",(session.get("admin_username") or "",)).fetchone()
                owner_user_id=owner_row["id"] if owner_row else None
            except Exception:
                owner_user_id=None
            now=datetime.utcnow().isoformat()
            conn.execute("UPDATE platform_spaces SET verification_status='verified',updated_at=? WHERE id=?",(now,space_id))
            if space["kind"]=="institution":
                conn.execute("UPDATE institutions SET verification_status='verified',verified_at=?,verified_by=? WHERE id=?",(now,owner_user_id,space["institution_id"]))
                conn.execute("UPDATE institution_memberships SET verification_status='verified',verified_at=?,verified_by=? WHERE institution_id=? AND role='institution_owner'",(now,owner_user_id,space["institution_id"]))
            else:
                conn.execute("UPDATE organizations SET verification_status='verified',verified_at=?,verified_by=? WHERE id=?",(now,owner_user_id,space["organization_id"]))
                conn.execute("UPDATE organization_memberships SET verification_status='verified',verified_at=?,verified_by=? WHERE organization_id=? AND role='organization_owner'",(now,owner_user_id,space["organization_id"]))
            conn.commit()
            flash(f"{space['display_name']} is now marked as verified.","success")
        except Exception:
            conn.rollback()
            app.logger.exception("Organization verification failed")
            flash("Verification failed. No changes were applied.","error")
        finally:
            conn.close()
        return redirect(url_for("admin_god"))

    @app.route("/admin/god/staff/add", methods=["POST"])
    @owner_required
    def admin_god_add_staff():
        username=request.form.get("username","").strip(); selected=[s for s in request.form.getlist("scope") if s in SCOPES]
        if not username or not selected: flash("Choose a user and at least one moderation responsibility.","error"); return redirect(url_for("admin_god"))
        conn=get_db_connection(); user=conn.execute("SELECT id,username FROM users WHERE lower(username)=lower(?)",(username,)).fetchone()
        if not user: conn.close(); flash("That user account does not exist.","error"); return redirect(url_for("admin_god"))
        existing_staff=conn.execute("SELECT id,status FROM admin_staff WHERE user_id=? OR lower(username)=lower(?) LIMIT 1",(user["id"],user["username"])).fetchone()
        if existing_staff:
            conn.close(); flash("That account is already present in the moderator team. Edit its responsibilities instead.","error"); return redirect(url_for("admin_god"))
        try:
            now=datetime.utcnow().isoformat(); cur=conn.execute("INSERT INTO admin_staff(user_id,username,role,status,created_by,created_at) VALUES(?,?,?,?,?,?)",(user["id"],user["username"],"moderator","active",session.get("admin_username"),now)); staff_id=cur.lastrowid
            for scope in selected: conn.execute("INSERT INTO moderator_scopes(staff_id,scope) VALUES(?,?)",(staff_id,scope))
            notify_staff(conn, user["id"], "You are now a moderator", "The System Owner assigned you moderation responsibilities: " + ", ".join(SCOPES[s] for s in selected) + ".")
            conn.commit(); flash(f"{user['username']} is now an active moderator.","success")
        except Exception: conn.rollback(); flash("Could not assign moderator: account may already be assigned.","error")
        finally: conn.close()
        return redirect(url_for("admin_god"))

    @app.route("/admin/god/staff/<int:staff_id>/scopes", methods=["POST"])
    @owner_required
    def admin_god_scopes(staff_id):
        selected=[s for s in request.form.getlist("scope") if s in SCOPES]; conn=get_db_connection(); row=conn.execute("SELECT user_id FROM admin_staff WHERE id=?",(staff_id,)).fetchone()
        if not row: conn.close(); abort(404)
        conn.execute("DELETE FROM moderator_scopes WHERE staff_id=?",(staff_id,))
        for scope in selected: conn.execute("INSERT INTO moderator_scopes(staff_id,scope) VALUES(?,?)",(staff_id,scope))
        conn.execute("INSERT INTO moderation_actions(staff_id,action,scope,target_type,target_id,note,created_at) VALUES(?,?,?,?,?,?,?)",(staff_id,"updated_scopes","staff","moderator",str(staff_id),"Responsibilities updated by System Owner",datetime.utcnow().isoformat()))
        notify_staff(conn, row["user_id"], "Moderator responsibilities updated", "Your moderation responsibilities were updated by the System Owner.")
        conn.commit(); conn.close(); flash("Moderator responsibilities updated.","success"); return redirect(url_for("admin_god"))

    @app.route("/admin/god/staff/<int:staff_id>/toggle", methods=["POST"])
    @owner_required
    def admin_god_toggle_staff(staff_id):
        conn=get_db_connection(); row=conn.execute("SELECT status FROM admin_staff WHERE id=?",(staff_id,)).fetchone()
        if row:
            new_status="suspended" if row["status"]=="active" else "active"
            conn.execute("UPDATE admin_staff SET status=? WHERE id=?",(new_status,staff_id)); conn.execute("INSERT INTO moderation_actions(staff_id,action,scope,target_type,target_id,note,created_at) VALUES(?,?,?,?,?,?,?)",(staff_id,"status_changed","staff","moderator",str(staff_id),new_status,datetime.utcnow().isoformat()))
            notify_staff(conn, row["user_id"], "Moderator access updated", "Your moderator access is now " + new_status + ".")
            conn.commit()
        conn.close(); return redirect(url_for("admin_god"))

    @app.route("/admin/god/staff/<int:staff_id>/remove", methods=["POST"])
    @owner_required
    def admin_god_remove_staff(staff_id):
        conn=get_db_connection(); row=conn.execute("SELECT user_id,username FROM admin_staff WHERE id=?",(staff_id,)).fetchone()
        if not row: conn.close(); abort(404)
        import app as app_module
        owner_username=getattr(app_module,"ADMIN_USER",os.environ.get("ADMIN_USER","admin")).strip()
        owner_user=conn.execute("SELECT id FROM users WHERE lower(username)=lower(?) LIMIT 1",(owner_username,)).fetchone()
        protected = bool(row["username"]) and row["username"].casefold() == owner_username.casefold()
        if owner_user and row["user_id"] and int(owner_user["id"]) == int(row["user_id"]):
            protected = True
        if protected:
            conn.close(); flash("The System Owner identity is protected and cannot be removed from the moderator team.","error"); return redirect(url_for("admin_god"))
        username=row["username"]
        try:
            conn.execute("INSERT INTO moderation_actions(staff_id,action,scope,target_type,target_id,note,created_at) VALUES(?,?,?,?,?,?,?)",(staff_id,"moderator_removed","all","staff",str(staff_id),f"{username} removed by System Owner",datetime.utcnow().isoformat()))
            notify_staff(conn, row["user_id"], "Moderator access removed", "The System Owner removed your moderator assignment.")
            conn.execute("DELETE FROM moderator_work WHERE staff_id=?",(staff_id,))
            conn.execute("DELETE FROM moderator_scopes WHERE staff_id=?",(staff_id,))
            conn.execute("DELETE FROM admin_staff WHERE id=?",(staff_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            app.logger.exception("Moderator removal failed")
            flash("Moderator removal failed. No changes were applied.","error")
            return redirect(url_for("admin_god"))
        conn.close()
        flash(f"{username} was removed from the moderator team.","success")
        return redirect(url_for("admin_god"))

    @app.route("/admin/god/work/add", methods=["POST"])
    @owner_required
    def admin_god_add_work():
        staff_id=request.form.get("staff_id",type=int); scope=request.form.get("scope",""); title=request.form.get("title","").strip(); description=request.form.get("description","").strip()
        if not staff_id or scope not in SCOPES or not title: flash("Work needs a moderator, responsibility and title.","error"); return redirect(url_for("admin_god"))
        conn=get_db_connection(); allowed=conn.execute("SELECT 1 FROM moderator_scopes WHERE staff_id=? AND scope=?",(staff_id,scope)).fetchone(); status=conn.execute("SELECT status,user_id FROM admin_staff WHERE id=?",(staff_id,)).fetchone()
        if not allowed or not status or status["status"]!="active": conn.close(); flash("Moderator is not assigned that responsibility.","error"); return redirect(url_for("admin_god"))
        conn.execute("INSERT INTO moderator_work(staff_id,scope,title,description,status,created_by,created_at) VALUES(?,?,?,?,?,?,?)",(staff_id,scope,title,description,"open",session.get("admin_username"),datetime.utcnow().isoformat()))
        notify_staff(conn, status["user_id"], "New moderation work assigned", title + (": " + description if description else ""))
        conn.commit(); conn.close(); flash("Moderation work assigned.","success"); return redirect(url_for("admin_god"))

    @app.route("/moderator", methods=["GET"])
    def moderator_dashboard():
        staff=staff_identity()
        if not staff: return abort(403)
        conn=get_db_connection(); scopes=[x["scope"] for x in conn.execute("SELECT scope FROM moderator_scopes WHERE staff_id=?",(staff["id"],)).fetchall()]; work=conn.execute("SELECT * FROM moderator_work WHERE staff_id=? ORDER BY CASE WHEN status='open' THEN 0 ELSE 1 END,id DESC",(staff["id"],)).fetchall(); actions=conn.execute("SELECT * FROM moderation_actions WHERE staff_id=? ORDER BY id DESC LIMIT 20",(staff["id"],)).fetchall(); conn.close()
        return render_template("moderator_dashboard.html",staff=staff,scopes=scopes,scope_labels=SCOPES,actions=actions,work=work,csrf=session.get("csrf_token",""))

    @app.route("/moderator/work/<int:work_id>/complete", methods=["POST"])
    def moderator_complete_work(work_id):
        staff=staff_identity()
        if not staff or not csrf_ok(): return abort(403)
        conn=get_db_connection(); row=conn.execute("SELECT * FROM moderator_work WHERE id=? AND staff_id=?",(work_id,staff["id"])).fetchone()
        if not row: conn.close(); return abort(404)
        conn.execute("UPDATE moderator_work SET status='completed',completed_at=? WHERE id=?",(datetime.utcnow().isoformat(),work_id)); conn.execute("INSERT INTO moderation_actions(staff_id,action,scope,target_type,target_id,note,created_at) VALUES(?,?,?,?,?,?,?)",(staff["id"],"completed_work",row["scope"],"work",str(work_id),row["title"],datetime.utcnow().isoformat())); conn.commit(); conn.close(); return redirect(url_for("moderator_dashboard"))

    return True
