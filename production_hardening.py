"""Production hardening helpers for UniversityConnect."""
import logging
import os
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit
from flask import abort, jsonify, redirect, request, session

log = logging.getLogger("universityconnect.security")
_RATE_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))
_RATE_MAX = int(os.environ.get("RATE_LIMIT_MAX_REQUESTS", "120"))
_BUCKETS = defaultdict(deque)

def _client_ip():
    return request.remote_addr or "unknown"

def _rate_limited():
    if request.endpoint in {"static", "health", "ready"}:
        return False
    now = time.monotonic()
    key = (_client_ip(), request.endpoint or request.path)
    bucket = _BUCKETS[key]
    cutoff = now - _RATE_WINDOW
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()
    if len(bucket) >= _RATE_MAX:
        return True
    bucket.append(now)
    if len(_BUCKETS) > 10000:
        for k in list(_BUCKETS)[:1000]:
            if not _BUCKETS[k]:
                _BUCKETS.pop(k, None)
    return False

def _same_origin():
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return True
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    host = request.host
    for value in (origin, referer):
        if not value:
            continue
        try:
            parsed = urlsplit(value)
            if parsed.netloc and parsed.netloc != host:
                return False
        except ValueError:
            return False
    return True

def install(app, get_db_connection, admin_username):
    @app.before_request
    def _production_request_guard():
        if os.environ.get("APP_ENV", "development").lower() == "production":
            if os.environ.get("REQUIRE_HTTPS", "0") == "1" and not request.is_secure:
                return redirect(request.url.replace("http://", "https://", 1), code=308)
        if not _same_origin():
            abort(403)
        if _rate_limited():
            response = jsonify(error="Too many requests. Please try again later.")
            response.status_code = 429
            response.headers["Retry-After"] = str(_RATE_WINDOW)
            return response

    @app.after_request
    def _production_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if os.environ.get("APP_ENV", "development").lower() == "production" and request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.route("/health", methods=["GET"])
    def health():
        try:
            conn = get_db_connection()
            conn.execute("SELECT 1").fetchone()
            conn.close()
            return jsonify(status="ok", database="ok"), 200
        except Exception:
            log.exception("Health check database failure")
            return jsonify(status="degraded", database="error"), 503

    @app.route("/ready", methods=["GET"])
    def ready():
        try:
            conn = get_db_connection()
            conn.execute("SELECT 1").fetchone()
            conn.close()
            return jsonify(status="ready"), 200
        except Exception:
            log.exception("Readiness check failure")
            return jsonify(status="not_ready"), 503

    def hardened_admin_required():
        return session.get("logged_in") is True and session.get("admin_username") == admin_username

    app.extensions["hardened_admin_required"] = hardened_admin_required
    return hardened_admin_required

def require_same_tenant(conn, current_user_id, target_user_id):
    if not current_user_id or not target_user_id:
        abort(403)
    if int(current_user_id) == int(target_user_id):
        return True
    row = conn.execute("SELECT university_id FROM users WHERE id = ?", (current_user_id,)).fetchone()
    target = conn.execute("SELECT university_id FROM users WHERE id = ?", (target_user_id,)).fetchone()
    if not row or not target:
        abort(404)
    if row["university_id"] is None or target["university_id"] is None:
        abort(403)
    if int(row["university_id"]) != int(target["university_id"]):
        abort(403)
    return True
