"""Versioned JSON API used by native/mobile clients."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, abort, g, jsonify, request, send_file, url_for, redirect
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

ACCESS_TTL = max(300, int(os.environ.get("MOBILE_ACCESS_TTL_SECONDS", "1800")))
REFRESH_TTL = max(3600, int(os.environ.get("MOBILE_REFRESH_TTL_SECONDS", str(60 * 60 * 24 * 30))))
MEDIA_URL_TTL = max(60, min(3600, int(os.environ.get("MOBILE_MEDIA_URL_TTL_SECONDS", "300")))


def install(app_module):
    app = app_module.app
    secret = getattr(app, "secret_key", None) or os.environ.get("FLASK_SECRET_KEY")
    if not secret:
        raise RuntimeError("A stable Flask secret is required for the mobile API.")
    bp.token_serializer = URLSafeTimedSerializer(secret, salt="university-connect-mobile-v1")
    bp.media_serializer = URLSafeTimedSerializer(secret, salt="university-connect-media-v1")
    bp.app_module = app_module

    conn = app_module.get_db_connection()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS mobile_refresh_tokens (
            jti TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            user_agent TEXT
        )"""
    )
    conn.commit()
    conn.close()
    if "api_v1" not in app.blueprints:
        app.register_blueprint(bp)


def now_utc():
    return datetime.utcnow().replace(microsecond=0)


def db_now():
    return now_utc().strftime("%Y-%m-%d %H:%M:%S")


def json_error(message, status=400):
    return jsonify({"error": message}), status


def token_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def issue_access(uid):
    payload = {
        "sub": int(uid), "typ": "access",
        "jti": secrets.token_urlsafe(12),
        "iat": int(now_utc().timestamp()),
    }
    return bp.token_serializer.dumps(payload)


def issue_refresh(uid, user_agent=""):
    jti = secrets.token_urlsafe(18)
    value = bp.token_serializer.dumps({
        "sub": int(uid), "typ": "refresh", "jti": jti,
        "iat": int(now_utc().timestamp()),
    })
    created = now_utc()
    expires = created + timedelta(seconds=REFRESH_TTL)
    conn = bp.app_module.get_db_connection()
    conn.execute(
        "INSERT INTO mobile_refresh_tokens(jti,user_id,token_hash,created_at,expires_at,revoked_at,user_agent) VALUES(?,?,?,?,?,?,?)",
        (jti, int(uid), token_hash(value), created.strftime("%Y-%m-%d %H:%M:%S"),
         expires.strftime("%Y-%m-%d %H:%M:%S"), None, (user_agent or "")[:500]),
    )
    conn.commit()
    conn.close()
    return value


def revoke_refresh(value):
    try:
        payload = bp.token_serializer.loads(value, max_age=REFRESH_TTL)
    except (BadSignature, SignatureExpired):
        return
    jti = payload.get("jti")
    if not jti:
        return
    conn = bp.app_module.get_db_connection()
    conn.execute(
        "UPDATE mobile_refresh_tokens SET revoked_at=? WHERE jti=? AND token_hash=? AND revoked_at IS NULL",
        (db_now(), jti, token_hash(value)),
    )
    conn.commit()
    conn.close()


def bearer_uid():
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    value = header.split(" ", 1)[1].strip()
    if not value:
        return None
    try:
        payload = bp.token_serializer.loads(value, max_age=ACCESS_TTL)
    except (BadSignature, SignatureExpired):
        return None
    if payload.get("typ") != "access":
        return None
    try:
        return int(payload["sub"])
    except (TypeError, ValueError, KeyError):
        return None


def mobile_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        uid = bearer_uid()
        if not uid:
            return json_error("authentication_required", 401)
        conn = bp.app_module.get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        conn.close()
        if not user:
            return json_error("user_not_found", 401)
        g.mobile_user_id = uid
        g.mobile_user = user
        return view(*args, **kwargs)
    return wrapped


def mobile_user(uid):
    conn = bp.app_module.get_db_connection()
    row = conn.execute(
        "SELECT id,name,username,gmail,phone,photo,nickname,relationship_status,university_id,bio,headline,occupation,company,website,education,skills,experience,achievements,interests,projects,certifications,profile_view FROM users WHERE id=?",
        (uid,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def media_url(kind, media_token, uid):
    if not media_token:
        return None
    access = bp.media_serializer.dumps({"uid": int(uid), "kind": kind, "token": str(media_token)})
    return url_for(
        "api_v1.mobile_media",
        kind=kind,
        token=media_token,
        access=access,
        _external=True,
    )


def public_photo_url(photo):
    if not photo:
        return None
    return url_for("static", filename="uploads/" + str(photo), _external=True)


def serialize_post(row, uid):
    item = dict(row)
    return {
        "id": item["id"],
        "user_id": item["user_id"],
        "name": item["name"],
        "username": item["username"],
        "photo_url": public_photo_url(item.get("photo")),
        "caption": item.get("caption") or "",
        "media_type": item.get("media_type") or "image",
        "post_type": item.get("post_type") or "post",
        "media_url": media_url("post", item.get("media_token"), uid) if item.get("original_name") else None,
        "created_at": item.get("created_at"),
        "like_count": int(item.get("like_count") or 0),
        "comment_count": int(item.get("comment_count") or 0),
        "liked_by_me": bool(item.get("liked_by_me")),
        "is_owner": int(item["user_id"]) == int(uid),
        "comments": item.get("comments", []),
    }


def serialize_story(row, uid):
    item = dict(row)
    return {
        "id": item["id"],
        "user_id": item["user_id"],
        "name": item["name"],
        "username": item["username"],
        "photo_url": public_photo_url(item.get("photo")),
        "media_type": item["media_type"],
        "caption": item.get("caption") or "",
        "media_url": media_url("story", item.get("media_token"), uid),
        "created_at": item.get("created_at"),
        "is_mine": bool(item.get("is_mine")),
    }


def build_posts(uid, mode="feed", offset=0, limit=12):
    conn = bp.app_module.get_db_connection()
    fetcher = bp.app_module.fetch_reels if mode == "reels" else bp.app_module.fetch_feed
    rows = fetcher(conn, uid, offset, limit)
    raw = [dict(x) for x in rows]
    result = []
    if raw:
        ids = [int(x["id"]) for x in raw]
        placeholders = ",".join("?" for _ in ids)
        comments = conn.execute(
            f"""SELECT post_id,comment,created_at,name,photo
                FROM (
                    SELECT pc.post_id,pc.comment,pc.created_at,u.name,u.photo,
                           ROW_NUMBER() OVER (PARTITION BY pc.post_id ORDER BY pc.id DESC) rn
                    FROM post_comments pc JOIN users u ON u.id=pc.user_id
                    WHERE pc.post_id IN ({placeholders})
                ) recent WHERE rn<=3 ORDER BY post_id,rn""",
            tuple(ids),
        ).fetchall()
        comment_map = {}
        for c in comments:
            comment_map.setdefault(int(c["post_id"]), []).append({
                "name": c["name"], "comment": c["comment"], "created_at": c["created_at"],
                "photo_url": public_photo_url(c["photo"]),
            })
        for x in raw:
            x["comments"] = comment_map.get(int(x["id"]), [])
            result.append(serialize_post(x, uid))
    conn.close()
    return result


@bp.get("/health")
def health():
    conn = bp.app_module.get_db_connection()
    try:
        conn.execute("SELECT 1").fetchone()
        return jsonify({"ok": True, "service": "api-v1"})
    finally:
        conn.close()


@bp.post("/auth/login")
def auth_login():
    data = request.get_json(silent=True) or {}
    identifier = str(data.get("identifier", "")).strip()
    password = str(data.get("password", ""))
    if not identifier or not password:
        return json_error("identifier_and_password_required", 400)
    phone_identifier = bp.app_module.normalize_phone(identifier)
    conn = bp.app_module.get_db_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE lower(gmail)=? OR lower(username)=? OR phone=?",
        (identifier.lower(), identifier.lower(), phone_identifier),
    ).fetchone()
    conn.close()
    if not user or not user["password_hash"] or not check_password_hash(user["password_hash"], password):
        return json_error("invalid_credentials", 401)
    access = issue_access(user["id"])
    refresh = issue_refresh(user["id"], request.headers.get("User-Agent", ""))
    return jsonify({
        "access_token": access, "refresh_token": refresh, "token_type": "Bearer",
        "expires_in": ACCESS_TTL, "refresh_expires_in": REFRESH_TTL,
        "user": mobile_user(user["id"]),
    })


@bp.post("/auth/refresh")
def auth_refresh():
    data = request.get_json(silent=True) or {}
    value = str(data.get("refresh_token", "")).strip()
    if not value:
        return json_error("refresh_token_required", 400)
    try:
        payload = bp.token_serializer.loads(value, max_age=REFRESH_TTL)
    except SignatureExpired:
        return json_error("refresh_token_expired", 401)
    except BadSignature:
        return json_error("invalid_refresh_token", 401)
    if payload.get("typ") != "refresh" or not payload.get("jti"):
        return json_error("invalid_refresh_token", 401)
    conn = bp.app_module.get_db_connection()
    row = conn.execute(
        "SELECT user_id,revoked_at FROM mobile_refresh_tokens WHERE jti=? AND token_hash=?",
        (payload["jti"], token_hash(value)),
    ).fetchone()
    if not row or row["revoked_at"]:
        conn.close()
        return json_error("refresh_token_revoked", 401)
    uid = int(row["user_id"])
    user = conn.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone()
    conn.execute("UPDATE mobile_refresh_tokens SET revoked_at=? WHERE jti=?", (db_now(), payload["jti"]))
    conn.commit()
    conn.close()
    if not user:
        return json_error("user_not_found", 401)
    return jsonify({
        "access_token": issue_access(uid),
        "refresh_token": issue_refresh(uid, request.headers.get("User-Agent", "")),
        "token_type": "Bearer", "expires_in": ACCESS_TTL, "refresh_expires_in": REFRESH_TTL,
    })


@bp.post("/auth/logout")
@mobile_auth
def auth_logout():
    data = request.get_json(silent=True) or {}
    value = str(data.get("refresh_token", "")).strip()
    if value:
        revoke_refresh(value)
    return jsonify({"ok": True})


@bp.get("/me")
@mobile_auth
def me():
    return jsonify({"user": mobile_user(g.mobile_user_id)})


@bp.get("/feed")
@mobile_auth
def feed():
    try:
        offset = max(0, int(request.args.get("offset", "0")))
    except ValueError:
        offset = 0
    limit = min(30, max(1, int(request.args.get("limit", "12"))))
    posts = build_posts(g.mobile_user_id, "feed", offset, limit)
    return jsonify({"posts": posts, "has_more": len(posts) == limit, "next_offset": offset + len(posts)})


@bp.get("/reels")
@mobile_auth
def reels():
    try:
        offset = max(0, int(request.args.get("offset", "0")))
    except ValueError:
        offset = 0
    limit = min(30, max(1, int(request.args.get("limit", "12"))))
    posts = build_posts(g.mobile_user_id, "reels", offset, limit)
    return jsonify({"posts": posts, "has_more": len(posts) == limit, "next_offset": offset + len(posts)})


@bp.get("/stories")
@mobile_auth
def stories():
    conn = bp.app_module.get_db_connection()
    rows = bp.app_module.fetch_stories(conn, g.mobile_user_id)
    result = [serialize_story(x, g.mobile_user_id) for x in rows]
    conn.close()
    return jsonify({"stories": result})


def _save_upload(file_storage, mode):
    if not file_storage or not file_storage.filename:
        return json_error("media_required", 400)
    original = secure_filename(file_storage.filename)
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if mode == "reel":
        allowed = bp.app_module.POST_VIDEO_EXTENSIONS
    else:
        allowed = bp.app_module.POST_IMAGE_EXTENSIONS | bp.app_module.POST_VIDEO_EXTENSIONS
    if ext not in allowed:
        return json_error("unsupported_media_format", 400)
    media_type = "video" if ext in bp.app_module.POST_VIDEO_EXTENSIONS else "image"
    if mode == "reel" and media_type != "video":
        return json_error("reel_requires_video", 400)
    if not bp.app_module.validate_media_signature(file_storage, media_type):
        return json_error("media_signature_invalid", 400)
    token = secrets.token_urlsafe(36)
    try:
        bp.app_module.save_private_media(file_storage, token, original)
        return token, original, media_type
    except Exception:
        bp.app_module.remove_private_media(token, original)
        raise


@bp.post("/posts")
@mobile_auth
def create_post():
    caption = request.form.get("caption", "").strip()[:2000]
    mode = request.form.get("post_type", "post").strip().lower()
    if mode not in {"post", "reel"}:
        mode = "post"
    media = request.files.get("media")
    if not caption and not (media and media.filename):
        return json_error("caption_or_media_required", 400)
    token = ""
    original = ""
    try:
        media_type = "image"
        if media and media.filename:
            saved = _save_upload(media, mode)
            if not isinstance(saved, tuple):
                return saved
            token, original, media_type = saved
        elif mode == "reel":
            return json_error("reel_requires_video", 400)
        conn = bp.app_module.get_db_connection()
        cursor = conn.execute(
            "INSERT INTO posts(user_id,caption,media_token,media_type,original_name,post_type,created_at) VALUES(?,?,?,?,?,?,?)",
            (g.mobile_user_id, caption, token, media_type, original, mode, db_now()),
        )
        post_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return jsonify({
            "ok": True, "post_id": post_id, "post_type": mode,
            "media_url": media_url("post", token, g.mobile_user_id) if token else None,
        }), 201
    except Exception:
        if token:
            bp.app_module.remove_private_media(token, original)
        raise


@bp.post("/stories")
@mobile_auth
def create_story():
    media = request.files.get("media")
    caption = request.form.get("caption", "").strip()[:500]
    if not media or not media.filename:
        return json_error("media_required", 400)
    original = secure_filename(media.filename)
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in (bp.app_module.POST_IMAGE_EXTENSIONS | bp.app_module.POST_VIDEO_EXTENSIONS):
        return json_error("unsupported_media_format", 400)
    media_type = "video" if ext in bp.app_module.POST_VIDEO_EXTENSIONS else "image"
    if not bp.app_module.validate_media_signature(media, media_type):
        return json_error("media_signature_invalid", 400)
    token = secrets.token_urlsafe(36)
    try:
        bp.app_module.save_private_media(media, token, original)
        now = now_utc()
        expires = now + timedelta(hours=24)
        conn = bp.app_module.get_db_connection()
        cursor = conn.execute(
            "INSERT INTO stories(user_id,media_token,media_type,original_name,caption,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
            (g.mobile_user_id, token, media_type, original, caption,
             now.strftime("%Y-%m-%d %H:%M:%S"), expires.strftime("%Y-%m-%d %H:%M:%S")),
        )
        story_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return jsonify({
            "ok": True, "story_id": story_id,
            "media_url": media_url("story", token, g.mobile_user_id),
        }), 201
    except Exception:
        bp.app_module.remove_private_media(token, original)
        raise


@bp.post("/posts/<int:post_id>/like")
@mobile_auth
def like_post(post_id):
    conn = bp.app_module.get_db_connection()
    uid = g.mobile_user_id
    post = conn.execute(
        "SELECT p.id,p.user_id,u.university_id FROM posts p JOIN users u ON u.id=p.user_id WHERE p.id=?",
        (post_id,),
    ).fetchone()
    viewer = conn.execute("SELECT university_id FROM users WHERE id=?", (uid,)).fetchone()
    if not post:
        conn.close()
        return json_error("post_not_found", 404)
    if bp.app_module.users_are_blocked(conn, uid, int(post["user_id"])):
        conn.close()
        return json_error("blocked", 403)
    if post["university_id"] and viewer and viewer["university_id"] and int(post["university_id"]) != int(viewer["university_id"]):
        conn.close()
        return json_error("not_allowed", 403)
    exists = conn.execute("SELECT 1 FROM post_likes WHERE post_id=? AND user_id=?", (post_id, uid)).fetchone()
    if exists:
        conn.execute("DELETE FROM post_likes WHERE post_id=? AND user_id=?", (post_id, uid))
        liked = False
    else:
        conn.execute("INSERT INTO post_likes(post_id,user_id,created_at) VALUES(?,?,?)", (post_id, uid, db_now()))
        liked = True
    count = conn.execute("SELECT COUNT(*) FROM post_likes WHERE post_id=?", (post_id,)).fetchone()[0]
    conn.commit()
    conn.close()
    return jsonify({"liked": liked, "like_count": count})


@bp.post("/posts/<int:post_id>/comments")
@mobile_auth
def comment_post(post_id):
    comment = request.form.get("comment", "").strip() if request.form else ""
    if not comment:
        data = request.get_json(silent=True) or {}
        comment = str(data.get("comment", "")).strip()
    if not comment or len(comment) > 1000:
        return json_error("invalid_comment", 400)
    conn = bp.app_module.get_db_connection()
    uid = g.mobile_user_id
    post = conn.execute(
        "SELECT p.id,p.user_id,u.university_id FROM posts p JOIN users u ON u.id=p.user_id WHERE p.id=?",
        (post_id,),
    ).fetchone()
    viewer = conn.execute("SELECT university_id FROM users WHERE id=?", (uid,)).fetchone()
    if not post:
        conn.close()
        return json_error("post_not_found", 404)
    if bp.app_module.users_are_blocked(conn, uid, int(post["user_id"])):
        conn.close()
        return json_error("blocked", 403)
    if post["university_id"] and viewer and viewer["university_id"] and int(post["university_id"]) != int(viewer["university_id"]):
        conn.close()
        return json_error("not_allowed", 403)
    conn.execute(
        "INSERT INTO post_comments(post_id,user_id,comment,created_at) VALUES(?,?,?,?)",
        (post_id, uid, comment, db_now()),
    )
    count = conn.execute("SELECT COUNT(*) FROM post_comments WHERE post_id=?", (post_id,)).fetchone()[0]
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "comment_count": count})


@bp.delete("/posts/<int:post_id>")
@mobile_auth
def delete_post(post_id):
    conn = bp.app_module.get_db_connection()
    post = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not post:
        conn.close()
        return json_error("post_not_found", 404)
    if int(post["user_id"]) != int(g.mobile_user_id):
        conn.close()
        return json_error("not_allowed", 403)
    conn.execute("DELETE FROM post_likes WHERE post_id=?", (post_id,))
    conn.execute("DELETE FROM post_comments WHERE post_id=?", (post_id,))
    conn.execute("DELETE FROM posts WHERE id=?", (post_id,))
    conn.commit()
    conn.close()
    bp.app_module.remove_private_media(post["media_token"], post["original_name"])
    return jsonify({"ok": True})


@bp.get("/notifications")
@mobile_auth
def notifications():
    uid = g.mobile_user_id
    conn = bp.app_module.get_db_connection()
    out = []
    try:
        rows = conn.execute(
            "SELECT id,title,message,created_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50",
            (uid,),
        ).fetchall()
        out.extend({"source": "social", **dict(row)} for row in rows)
    except Exception:
        pass
    try:
        rows = conn.execute(
            "SELECT id,kind,title,body AS message,url,created_at,is_read FROM platform_notifications WHERE user_id=? ORDER BY id DESC LIMIT 50",
            (uid,),
        ).fetchall()
        out.extend({"source": "platform", **dict(row)} for row in rows)
    except Exception:
        pass
    conn.close()
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return jsonify({"notifications": out[:50]})


@bp.get("/messages/<int:other_user_id>")
@mobile_auth
def messages(other_user_id):
    uid = g.mobile_user_id
    if other_user_id == uid:
        return json_error("invalid_recipient", 400)
    conn = bp.app_module.get_db_connection()
    if not conn.execute("SELECT id FROM users WHERE id=?", (other_user_id,)).fetchone():
        conn.close()
        return json_error("user_not_found", 404)
    blocked = bp.app_module.users_are_blocked(conn, uid, other_user_id)
    now = db_now()
    conn.execute("DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at<=?", (now,))
    if not blocked:
        conn.execute(
            "UPDATE messages SET delivered_at=COALESCE(delivered_at,?),is_read=1,read_at=COALESCE(read_at,?) WHERE sender_id=? AND receiver_id=?",
            (now, now, other_user_id, uid),
        )
    try:
        since_id = max(0, int(request.args.get("since_id", "0")))
    except ValueError:
        since_id = 0
    rows = conn.execute(
        """SELECT id,sender_id,receiver_id,message,ciphertext,iv,encryption_version,created_at,expires_at,is_read,delivered_at,read_at
           FROM messages WHERE id>? AND ((sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?))
           ORDER BY id ASC LIMIT 200""",
        (since_id, uid, other_user_id, other_user_id, uid),
    ).fetchall()
    conn.commit()
    conn.close()
    return jsonify({"messages": [dict(x) for x in rows], "blocked": bool(blocked)})


@bp.post("/messages/<int:receiver_id>")
@mobile_auth
def send_message(receiver_id):
    uid = g.mobile_user_id
    if receiver_id == uid:
        return json_error("invalid_recipient", 400)
    data = request.get_json(silent=True) or {}
    form = request.form if request.form else {}
    message_text = str(form.get("message", form.get("body", ""))).strip()
    if not message_text:
        message_text = str(data.get("message", data.get("body", ""))).strip()
    ciphertext = str(data.get("ciphertext", form.get("ciphertext", ""))).strip()
    iv = str(data.get("iv", form.get("iv", ""))).strip()

    conn = bp.app_module.get_db_connection()
    if not conn.execute("SELECT id FROM users WHERE id=?", (receiver_id,)).fetchone():
        conn.close()
        return json_error("recipient_not_found", 404)
    if bp.app_module.users_are_blocked(conn, uid, receiver_id):
        conn.close()
        return json_error("blocked", 403)
    pref = conn.execute(
        "SELECT e2ee_enabled,disappearing_seconds FROM chat_preferences WHERE user_id=? AND peer_id=?",
        (uid, receiver_id),
    ).fetchone()
    secure = bool(pref and pref["e2ee_enabled"])
    if secure:
        if not ciphertext or not iv:
            conn.close()
            return json_error("secure_chat_requires_ciphertext", 400)
        stored = "[Encrypted message]"
        enc_ver = 1
        ciphertext = ciphertext[:20000]
        iv = iv[:100]
    else:
        if not message_text:
            conn.close()
            return json_error("message_required", 400)
        if len(message_text) > 5000:
            conn.close()
            return json_error("message_too_long", 400)
        stored = message_text
        enc_ver = 0
        ciphertext = None
        iv = None
    created = db_now()
    disappear = int(pref["disappearing_seconds"] if pref else 0)
    expires = None
    if disappear > 0:
        expires = (now_utc() + timedelta(seconds=disappear)).strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        "INSERT INTO messages(sender_id,receiver_id,message,ciphertext,iv,encryption_version,created_at,expires_at,is_read,delivered_at,read_at) VALUES(?,?,?,?,?,?,?,?,0,NULL,NULL)",
        (uid, receiver_id, stored, ciphertext, iv, enc_ver, created, expires),
    )
    mid = cursor.lastrowid
    conn.commit()
    conn.close()
    return jsonify({
        "ok": True,
        "message": {
            "id": mid, "sender_id": uid, "receiver_id": receiver_id, "message": stored,
            "ciphertext": ciphertext, "iv": iv, "encryption_version": enc_ver,
            "created_at": created, "expires_at": expires, "is_read": 0, "delivered_at": None,
        },
    }), 201


@bp.get("/workspace")
@mobile_auth
def workspace():
    uid = g.mobile_user_id
    conn = bp.app_module.get_db_connection()

    def safe(sql, args=()):
        try:
            return [dict(x) for x in conn.execute(sql, args).fetchall()]
        except Exception:
            return []

    institutions = safe(
        """SELECT i.id,i.name,i.slug,im.role,im.status
           FROM institution_memberships im JOIN institutions i ON i.id=im.institution_id
           WHERE im.user_id=? ORDER BY i.id DESC LIMIT 20""", (uid,),
    )
    organizations = safe(
        """SELECT o.id,o.name,o.slug,om.role,om.status
           FROM organization_memberships om JOIN organizations o ON o.id=om.organization_id
           WHERE om.user_id=? ORDER BY o.id DESC LIMIT 20""", (uid,),
    )
    jobs = safe("SELECT j.id,j.title,j.organization_id,j.status,j.location,j.created_at FROM jobs j ORDER BY j.id DESC LIMIT 30")
    applications = safe(
        """SELECT ja.id,ja.job_id,ja.status,ja.created_at,j.title
           FROM job_applications ja JOIN jobs j ON j.id=ja.job_id
           WHERE ja.user_id=? ORDER BY ja.id DESC LIMIT 30""", (uid,),
    )
    cv = safe("SELECT * FROM cv_profiles WHERE user_id=? LIMIT 1", (uid,))
    documents = safe(
        "SELECT id,title,document_type,created_at FROM cv_documents WHERE user_id=? ORDER BY id DESC LIMIT 30",
        (uid,),
    )
    conversations = safe(
        """SELECT c.id,c.subject,c.context_type,c.context_id,c.created_at
           FROM conversations c JOIN conversation_members cm ON cm.conversation_id=c.id
           WHERE cm.user_id=? ORDER BY c.id DESC LIMIT 30""", (uid,),
    )
    conn.close()
    return jsonify({
        "institutions": institutions, "organizations": organizations,
        "jobs": jobs, "applications": applications,
        "cv": cv[0] if cv else None, "documents": documents,
        "conversations": conversations,
    })


@bp.get("/media/<kind>/<token>")
def mobile_media(kind, token):
    access = request.args.get("access", "")
    if kind not in {"post", "story"} or not access:
        abort(404)
    try:
        payload = bp.media_serializer.loads(access, max_age=MEDIA_URL_TTL)
    except (BadSignature, SignatureExpired):
        abort(404)
    if payload.get("kind") != kind or payload.get("token") != token:
        abort(404)
    try:
        uid = int(payload["uid"])
    except (TypeError, ValueError, KeyError):
        abort(404)

    conn = bp.app_module.get_db_connection()
    if kind == "post":
        row = conn.execute(
            "SELECT p.*,u.university_id FROM posts p JOIN users u ON u.id=p.user_id WHERE p.media_token=?",
            (token,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT s.*,u.university_id FROM stories s JOIN users u ON u.id=s.user_id WHERE s.media_token=?",
            (token,),
        ).fetchone()
    viewer = conn.execute("SELECT university_id FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        conn.close()
        abort(404)
    if bp.app_module.users_are_blocked(conn, uid, int(row["user_id"])):
        conn.close()
        abort(403)
    if row["university_id"] and viewer and viewer["university_id"] and int(row["university_id"]) != int(viewer["university_id"]):
        conn.close()
        abort(403)
    if kind == "story" and row["expires_at"] <= db_now():
        conn.close()
        abort(404)
    original = row["original_name"]
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if not re.fullmatch(r"[a-z0-9]{1,8}", ext):
        conn.close()
        abort(404)
    key = bp.app_module.private_media_key(token, original)
    conn.close()
    provider = os.environ.get("MEDIA_STORAGE", "local").strip().lower()
    if provider in {"s3", "r2", "b2"}:
        try:
            return redirect(bp.app_module.get_media_storage().presigned_get_url(key, expires=MEDIA_URL_TTL))
        except Exception:
            abort(404)
    path = os.path.join(bp.app_module.PRIVATE_MEDIA_FOLDER, key)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, conditional=True, max_age=0)
