import os
import sqlite3
import re
import uuid
import secrets
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime
from urllib.parse import urlsplit

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
    make_response,
    send_file,
    abort
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix


app = Flask(__name__)

# Only enable ProxyFix when this app is actually behind a trusted reverse proxy.
# Never enable this on a directly exposed development server.
if os.environ.get("TRUST_PROXY", "0") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
APP_ENV = os.environ.get("APP_ENV", "development").lower()

if APP_ENV == "production" and not FLASK_SECRET_KEY:
    raise RuntimeError(
        "FLASK_SECRET_KEY must be set when APP_ENV=production."
    )

# Keep development sessions stable across normal Flask restarts. A random key
# generated on every boot signs users out whenever the process restarts, which
# is especially disruptive during local development. Production still requires
# an explicit FLASK_SECRET_KEY.
if FLASK_SECRET_KEY:
    app.secret_key = FLASK_SECRET_KEY
else:
    DEV_SECRET_FILE = Path(os.environ.get("DEV_SECRET_FILE", ".flask_secret_key"))
    try:
        if DEV_SECRET_FILE.exists():
            app.secret_key = DEV_SECRET_FILE.read_text(encoding="utf-8").strip()
        else:
            app.secret_key = secrets.token_hex(32)
            DEV_SECRET_FILE.write_text(app.secret_key, encoding="utf-8")
            try:
                os.chmod(DEV_SECRET_FILE, 0o600)
            except OSError:
                pass
    except OSError:
        # Read-only environments can still start, but sessions may not survive
        # a restart there.
        app.secret_key = secrets.token_hex(32)

UPLOAD_FOLDER = os.path.join("static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
POST_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
POST_VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "m4v"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"
app.config["SESSION_COOKIE_NAME"] = "university_session"
app.config["SESSION_COOKIE_PATH"] = "/"
app.config["SESSION_COOKIE_REFRESH_EACH_REQUEST"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 7
REQUIRE_HTTPS = os.environ.get("REQUIRE_HTTPS", "0") == "1"

DB_FILE = os.environ.get("DB_FILE", "database.db")
PRIVATE_MEDIA_FOLDER = os.path.abspath(os.path.join("private_media", "posts"))

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PRIVATE_MEDIA_FOLDER, exist_ok=True)


# ---------------------------------------------------------
# Admin configuration
# ---------------------------------------------------------

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")

ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH")

if not ADMIN_PASSWORD_HASH:
    if APP_ENV == "production":
        raise RuntimeError(
            "ADMIN_PASSWORD_HASH must be set when APP_ENV=production."
        )
    # Development-only credential. Set ADMIN_PASSWORD_HASH before deployment.
    ADMIN_PASSWORD_HASH = generate_password_hash("dev-only-change-me")
    print(
        "WARNING: Using development-only admin password. "
        "Set ADMIN_PASSWORD_HASH before deployment."
    )


# ---------------------------------------------------------
# Database
# ---------------------------------------------------------

# ---------------------------------------------------------
# Database compatibility layer
# ---------------------------------------------------------
# Local development keeps using SQLite. On Render, set DATABASE_URL to the
# managed PostgreSQL connection string. The compatibility layer intentionally
# keeps the existing SQL-heavy application intact while giving PostgreSQL
# sqlite-like rows and the small SQL differences used by this project.

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)


class CompatRow(dict):
    """sqlite3.Row-like mapping that also supports row[0]."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class PGCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self._lastrowid = None

    @property
    def lastrowid(self):
        return self._lastrowid

    def _wrap(self, row):
        if row is None:
            return None
        if isinstance(row, CompatRow):
            return row
        if isinstance(row, dict):
            return CompatRow(row)
        columns = [d.name for d in self._cursor.description] if self._cursor.description else []
        return CompatRow(zip(columns, row))

    def fetchone(self):
        return self._wrap(self._cursor.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self._cursor.fetchall()]

    def __iter__(self):
        for row in self._cursor:
            yield self._wrap(row)


class PGConnection:
    def __init__(self, url):
        import psycopg
        self._conn = psycopg.connect(url)

    @staticmethod
    def _sql(sql):
        # SQLite placeholders -> psycopg placeholders.
        sql = sql.replace("?", "%s")
        sql = sql.replace("COLLATE NOCASE", "")
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")

        # SQLite INSERT OR IGNORE -> PostgreSQL equivalent.
        if sql.lstrip().upper().startswith("INSERT OR IGNORE INTO"):
            sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO", 1)
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

        # The one INSERT OR REPLACE used by the application has a composite
        # primary key. Preserve its SQLite upsert behavior explicitly.
        if sql.lstrip().upper().startswith("INSERT OR REPLACE INTO GROUP_JOIN_REQUESTS"):
            sql = sql.replace("INSERT OR REPLACE INTO", "INSERT INTO", 1)
            sql = sql.rstrip().rstrip(";") + (
                " ON CONFLICT (group_id, user_id) DO UPDATE SET "
                "status=EXCLUDED.status, created_at=EXCLUDED.created_at"
            )
        return sql

    def execute(self, sql, params=()):
        normalized = sql.strip()
        # SQLite schema introspection used by the migration code.
        pragma_match = re.match(r"PRAGMA\s+TABLE_INFO\(([^)]+)\)", normalized, re.I)
        if pragma_match:
            table_name = pragma_match.group(1).strip().strip('"').lower()
            if not re.fullmatch(r"[a-z_][a-z0-9_]*", table_name):
                raise ValueError("Invalid table name in schema introspection")
            normalized = (
                "SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s "
                "ORDER BY ordinal_position"
            )
            params = (table_name,)

        cur = self._conn.cursor()
        cur.execute(self._sql(normalized), params)
        wrapper = PGCursor(cur)

        # Preserve the existing cursor.lastrowid calls. PostgreSQL's
        # sequence-backed identity is advanced by a successful INSERT.
        if normalized.lstrip().upper().startswith("INSERT INTO") and " RETURNING " not in normalized.upper():
            try:
                with self._conn.cursor() as id_cur:
                    id_cur.execute("SELECT lastval()")
                    wrapper._lastrowid = id_cur.fetchone()[0]
            except Exception:
                wrapper._lastrowid = None
        return wrapper

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_db_connection():
    if USE_POSTGRES:
        return PGConnection(DATABASE_URL)

    conn = sqlite3.connect(DB_FILE, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.DatabaseError:
        pass
    return conn


def init_db():
    conn = get_db_connection()

    # -----------------------------------------------------
    # Users
    # -----------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            gmail TEXT NOT NULL,
            photo TEXT NOT NULL,
            username TEXT DEFAULT '',
            password_hash TEXT DEFAULT NULL,
            age INTEGER DEFAULT NULL,
            nickname TEXT DEFAULT '',
            partner TEXT DEFAULT '',
            relationship_status TEXT DEFAULT 'Single'
        )
    """)

    # -----------------------------------------------------
    # Gallery
    # -----------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS gallery (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            image_path TEXT NOT NULL,
            FOREIGN KEY (user_id)
                REFERENCES users(id)
                ON DELETE CASCADE
        )
    """)

    # -----------------------------------------------------
    # Activity Log
    # -----------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_username TEXT NOT NULL,
            action TEXT NOT NULL,
            target_user_id INTEGER,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # Safe migration for databases created before messaging was added.
    existing_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(users)").fetchall()
    }
    if "password_hash" not in existing_columns:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT DEFAULT NULL")

    # Profile/CV fields. These are added safely for older databases.
    profile_columns = {
        "phone": "TEXT DEFAULT ''",
        "location": "TEXT DEFAULT ''",
        "bio": "TEXT DEFAULT ''",
        "headline": "TEXT DEFAULT ''",
        "occupation": "TEXT DEFAULT ''",
        "company": "TEXT DEFAULT ''",
        "website": "TEXT DEFAULT ''",
        "education": "TEXT DEFAULT ''",
        "skills": "TEXT DEFAULT ''",
        "experience": "TEXT DEFAULT ''",
        "achievements": "TEXT DEFAULT ''",
        "interests": "TEXT DEFAULT ''",
        "birth_date": "TEXT DEFAULT ''",
        "cover_photo": "TEXT DEFAULT ''",
        "profile_view": "TEXT DEFAULT 'facebook'"
    }
    for column, definition in profile_columns.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT DEFAULT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            delivered_at TEXT DEFAULT NULL,
            read_at TEXT DEFAULT NULL,
            FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (receiver_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    message_columns = {
        "delivered_at": "TEXT DEFAULT NULL",
        "read_at": "TEXT DEFAULT NULL",
        "expires_at": "TEXT DEFAULT NULL",
    }
    message_existing_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(messages)").fetchall()
    }
    for column, definition in message_columns.items():
        if column not in message_existing_columns:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {column} {definition}")

    # End-to-end encrypted messaging metadata. The server stores ciphertext
    # and public keys only; plaintext message content is never required in
    # production for E2EE conversations. Existing plaintext rows remain
    # readable for backward compatibility and can be migrated/cleared later.
    for column, definition in {
        "ciphertext": "TEXT DEFAULT NULL",
        "iv": "TEXT DEFAULT NULL",
        "encryption_version": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if column not in message_existing_columns:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {column} {definition}")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_keys (
            user_id INTEGER PRIMARY KEY,
            public_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_blocks (
            blocker_id INTEGER NOT NULL,
            blocked_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (blocker_id, blocked_id),
            FOREIGN KEY (blocker_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (blocked_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_preferences (
            user_id INTEGER NOT NULL,
            peer_id INTEGER NOT NULL,
            theme TEXT NOT NULL DEFAULT 'default',
            wallpaper TEXT NOT NULL DEFAULT 'none',
            disappearing_seconds INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, peer_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (peer_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    chat_pref_columns = {row["name"] for row in conn.execute("PRAGMA table_info(chat_preferences)").fetchall()}
    if "e2ee_enabled" not in chat_pref_columns:
        conn.execute("ALTER TABLE chat_preferences ADD COLUMN e2ee_enabled INTEGER NOT NULL DEFAULT 0")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            privacy TEXT NOT NULL DEFAULT 'open',
            created_by INTEGER NOT NULL,
            source_group_id INTEGER DEFAULT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (source_group_id) REFERENCES groups(id) ON DELETE SET NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_group_members (
            chat_group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            joined_at TEXT NOT NULL,
            PRIMARY KEY (chat_group_id,user_id),
            FOREIGN KEY (chat_group_id) REFERENCES chat_groups(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_group_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_group_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (chat_group_id) REFERENCES chat_groups(id) ON DELETE CASCADE,
            FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            device_id TEXT NOT NULL UNIQUE,
            device_name TEXT NOT NULL DEFAULT 'Browser',
            identity_public_key TEXT NOT NULL,
            signed_prekey TEXT DEFAULT NULL,
            one_time_prekey TEXT DEFAULT NULL,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS encrypted_key_backups (
            user_id INTEGER PRIMARY KEY,
            version INTEGER NOT NULL DEFAULT 1,
            salt TEXT NOT NULL,
            iv TEXT NOT NULL,
            ciphertext TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS message_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id INTEGER NOT NULL,
            reported_user_id INTEGER NOT NULL,
            message_id INTEGER DEFAULT NULL,
            reason TEXT NOT NULL,
            details TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            FOREIGN KEY (reporter_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (reported_user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE SET NULL
        )
    """)

    # University study fields.
    study_columns = {
        "study_status": "TEXT DEFAULT ''",
        "department": "TEXT DEFAULT ''",
        "academic_year": "TEXT DEFAULT ''",
        "semester": "TEXT DEFAULT ''",
        "student_id": "TEXT DEFAULT ''",
        "university": "TEXT DEFAULT ''"
    }
    for column, definition in study_columns.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

    cv_columns = {
        "projects": "TEXT DEFAULT ''",
        "certifications": "TEXT DEFAULT ''",
        "references_text": "TEXT DEFAULT ''",
        "career_objective": "TEXT DEFAULT ''"
    }
    for column, definition in cv_columns.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            caption TEXT DEFAULT '',
            media_token TEXT NOT NULL UNIQUE,
            media_type TEXT NOT NULL CHECK(media_type IN ('image','video')),
            original_name TEXT DEFAULT '',
            post_type TEXT DEFAULT 'post',
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    post_columns = {"post_type": "TEXT DEFAULT 'post'"}
    post_existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(posts)").fetchall()}
    for column, definition in post_columns.items():
        if column not in post_existing_columns:
            conn.execute(f"ALTER TABLE posts ADD COLUMN {column} {definition}")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            media_token TEXT NOT NULL UNIQUE,
            media_type TEXT NOT NULL CHECK(media_type IN ('image','video')),
            original_name TEXT DEFAULT '',
            caption TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS post_likes (
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (post_id, user_id),
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS post_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            comment TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # -----------------------------------------------------
    # Commercial foundation: universities, roles and campus modules.
    # These tables are additive so the existing MVP keeps working.
    # -----------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS universities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            domain TEXT DEFAULT '',
            logo TEXT DEFAULT '',
            description TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_roles (
            user_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, role_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            university_id INTEGER,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (university_id) REFERENCES universities(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS clubs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            university_id INTEGER,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY (university_id) REFERENCES universities(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS club_members (
            club_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            joined_at TEXT NOT NULL,
            PRIMARY KEY (club_id, user_id),
            FOREIGN KEY (club_id) REFERENCES clubs(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            university_id INTEGER,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            event_date TEXT NOT NULL,
            location TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY (university_id) REFERENCES universities(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_registrations (
            event_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            registered_at TEXT NOT NULL,
            PRIMARY KEY (event_id, user_id),
            FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    # -----------------------------------------------------
    # Commercial product modules: departments, batches, groups,
    # university admins, branding and reporting.
    # -----------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            university_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            code TEXT DEFAULT '',
            description TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(university_id, name),
            FOREIGN KEY (university_id) REFERENCES universities(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            university_id INTEGER NOT NULL,
            department_id INTEGER,
            name TEXT NOT NULL,
            start_year INTEGER,
            end_year INTEGER,
            created_at TEXT NOT NULL,
            UNIQUE(university_id, name),
            FOREIGN KEY (university_id) REFERENCES universities(id) ON DELETE CASCADE,
            FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE SET NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            university_id INTEGER NOT NULL,
            department_id INTEGER,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            privacy TEXT NOT NULL DEFAULT 'university',
            created_by INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (university_id) REFERENCES universities(id) ON DELETE CASCADE,
            FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE SET NULL,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_members (
            group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            joined_at TEXT NOT NULL,
            PRIMARY KEY(group_id,user_id),
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_admins (
            group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(group_id,user_id),
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_join_requests (
            group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            PRIMARY KEY(group_id,user_id),
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_post_likes (
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(post_id,user_id),
            FOREIGN KEY (post_id) REFERENCES group_posts(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_post_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            comment TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (post_id) REFERENCES group_posts(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS branding_settings (
            university_id INTEGER PRIMARY KEY,
            primary_color TEXT DEFAULT '#1456c4',
            secondary_color TEXT DEFAULT '#0f172a',
            accent_color TEXT DEFAULT '#22c55e',
            custom_domain TEXT DEFAULT '',
            logo TEXT DEFAULT '',
            FOREIGN KEY (university_id) REFERENCES universities(id) ON DELETE CASCADE
        )
    """)

    for column, definition in {
        'department_id': 'INTEGER DEFAULT NULL',
        'batch_id': 'INTEGER DEFAULT NULL'
    }.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            university_id INTEGER,
            plan TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'trial',
            starts_at TEXT NOT NULL,
            renews_at TEXT,
            FOREIGN KEY (university_id) REFERENCES universities(id) ON DELETE CASCADE
        )
    """)
    for role_name in ("SUPER_ADMIN", "UNIVERSITY_ADMIN", "DEPARTMENT_ADMIN", "MODERATOR", "TEACHER", "STUDENT", "ALUMNI"):
        conn.execute("INSERT OR IGNORE INTO roles(name) VALUES(?)", (role_name,))

    if "university_id" not in existing_columns:
        conn.execute("ALTER TABLE users ADD COLUMN university_id INTEGER DEFAULT NULL")

    # Admin-controlled website settings. The public application never exposes
    # a write path to this table.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS site_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO site_settings(key, value) VALUES('site_name', ?)",
        (os.environ.get("SITE_NAME", "University Connect"),)
    )

    conn.commit()
    conn.close()


init_db()


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]

app.jinja_env.globals["csrf_token"] = csrf_token

def get_site_name():
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM site_settings WHERE key='site_name'").fetchone()
    conn.close()
    return row["value"] if row else os.environ.get("SITE_NAME", "University Connect")


def safe_internal_url(candidate, fallback):
    """Return only a same-site relative URL; never redirect to an external host."""
    if not candidate:
        return fallback
    try:
        parsed = urlsplit(candidate)
        if parsed.scheme or parsed.netloc:
            return fallback
        path = parsed.path or "/"
        if not path.startswith("/") or path.startswith("//"):
            return fallback
        if path == request.path and (parsed.query or "") == request.query_string.decode("utf-8", "ignore"):
            return fallback
        return (
            path
            + (("?" + parsed.query) if parsed.query else "")
            + (("#" + parsed.fragment) if parsed.fragment else "")
        )
    except Exception:
        return fallback


@app.context_processor
def inject_navigation_context():
    if session.get("user_logged_in"):
        fallback = url_for("user_home")
    elif session.get("logged_in"):
        fallback = url_for("home")
    else:
        fallback = url_for("user_login")
    return {
        "site_name": get_site_name(),
        "return_url": safe_internal_url(request.referrer, fallback),
    }


def require_csrf():
    token = (
        request.form.get("csrf_token")
        or request.headers.get("X-CSRF-Token")
        or request.headers.get("X-CSRFToken")
    )
    if not token and request.is_json:
        try:
            token = (request.get_json(silent=True) or {}).get("csrf_token")
        except Exception:
            token = None
    expected = session.get("csrf_token", "")
    if not token or not expected or not secrets.compare_digest(str(token), str(expected)):
        abort(400, description="Invalid security token.")


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def validate_image_signature(file_storage):
    """Basic magic-byte validation for uploaded raster images."""
    try:
        pos = file_storage.stream.tell()
        header = file_storage.stream.read(16)
        file_storage.stream.seek(pos)
    except Exception:
        return False

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if header.startswith(b"\xff\xd8\xff"):
        return True
    if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
        return True
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return True
    return False


def validate_media_signature(file_storage, media_type):
    """Validate a small set of common image/video container signatures."""
    try:
        pos = file_storage.stream.tell()
        header = file_storage.stream.read(32)
        file_storage.stream.seek(pos)
    except Exception:
        return False
    if media_type == "image":
        return validate_image_signature(file_storage)
    # MP4/MOV/M4V use an ISO BMFF ftyp box; WEBM starts with EBML.
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return True
    return len(header) >= 12 and header[4:8] == b"ftyp"


def remove_private_media(token, original_name):
    if not token or not original_name:
        return
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    if not re.fullmatch(r"[a-z0-9]{1,8}", ext):
        return
    try:
        os.remove(os.path.join(PRIVATE_MEDIA_FOLDER, f"{token}.{ext}"))
    except OSError:
        pass


def cleanup_expired_stories(conn):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    expired = conn.execute("SELECT media_token, original_name FROM stories WHERE expires_at <= ?", (now,)).fetchall()
    for row in expired:
        remove_private_media(row["media_token"], row["original_name"])
    if expired:
        conn.execute("DELETE FROM stories WHERE expires_at <= ?", (now,))
    return len(expired)


def same_university_clause(current_user_row, target_alias="u"):
    """Return an optional SQL restriction for legacy rows without university_id."""
    if not current_user_row or not current_user_row["university_id"]:
        return "", []
    return f" AND ({target_alias}.university_id IS NULL OR {target_alias}.university_id = ?)", [current_user_row["university_id"]]


def generate_unique_filename(original_filename, prefix="file"):
    """
    Prevent two uploaded files with the same original name
    from overwriting one another.
    """
    safe_name = secure_filename(original_filename)

    if not safe_name:
        return None

    extension = safe_name.rsplit(".", 1)[1].lower()

    return f"{prefix}_{uuid.uuid4().hex}.{extension}"


def log_activity(action, description, target_user_id=None):
    """
    Store an administrative action in the activity log.
    """

    admin_username = session.get("admin_username", ADMIN_USER)

    conn = get_db_connection()

    conn.execute(
        """
        INSERT INTO activity_log
        (
            admin_username,
            action,
            target_user_id,
            description,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            admin_username,
            action,
            target_user_id,
            description,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    )

    conn.commit()
    conn.close()


def admin_required():
    return session.get("logged_in") is True


def notify_user(conn, user_id, title, body):
    """Create an in-app notification safely inside the caller's transaction."""
    if not user_id:
        return
    conn.execute(
        "INSERT INTO notifications(user_id,title,body,is_read,created_at) VALUES(?,?,?,?,?)",
        (user_id, title, body, 0, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
    )


def notify_group_admins(conn, group_id, title, body, exclude_user_id=None):
    rows = conn.execute(
        """SELECT DISTINCT u.id FROM users u
           JOIN group_admins ga ON ga.user_id=u.id
           WHERE ga.group_id=?
           UNION SELECT created_by FROM groups WHERE id=? AND created_by IS NOT NULL""",
        (group_id, group_id)
    ).fetchall()
    for row in rows:
        if exclude_user_id and row[0] == exclude_user_id:
            continue
        notify_user(conn, row[0], title, body)



# ---------------------------------------------------------
# Login
# ---------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():

    error = None

    if request.method == "POST":

        require_csrf()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if (
            username == ADMIN_USER
            and check_password_hash(ADMIN_PASSWORD_HASH, password)
        ):
            # Keep an existing student session intact. This allows admin and
            # student areas to stay open in separate tabs.
            session["logged_in"] = True
            session["admin_username"] = username
            session.permanent = True
            session.modified = True

            return redirect(url_for("home"))

        error = "Invalid admin credentials. Access denied."

    return render_template("login.html", error=error)


# ---------------------------------------------------------
# Logout
# ---------------------------------------------------------

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    session.pop("admin_username", None)
    session.modified = True
    return redirect(url_for("login"))



# ---------------------------------------------------------
# Public user registration
# ---------------------------------------------------------

def normalize_phone(value):
    """Normalize common Bangladesh and international phone formats to E.164.

    The registration widget normally submits +880..., but users can also
    type a local 11-digit Bangladesh number such as 01712345678. Supporting
    that server-side prevents the browser widget from becoming a hard
    dependency and fixes registration when the country selector is changed.
    """
    value = "".join((value or "").strip().split())
    value = re.sub(r"[()\-]", "", value)
    if value.startswith("00"):
        value = "+" + value[2:]
    if re.fullmatch(r"01[3-9]\d{8}", value):
        value = "+880" + value[1:]
    elif re.fullmatch(r"8801[3-9]\d{8}", value):
        value = "+" + value
    return value


def valid_phone(value):
    # E.164: + followed by country code and 7-14 subscriber digits.
    if not re.fullmatch(r"\+[1-9]\d{7,14}", value or ""):
        return False
    # Bangladesh mobile numbers must map to the familiar 01[3-9]XXXXXXXX pattern.
    if value.startswith("+880"):
        return bool(re.fullmatch(r"\+8801[3-9]\d{8}", value))
    return True


def phone_country_code(value):
    # Useful only as a display/fallback helper. The client widget is the
    # authoritative country selector for registration.
    m = re.match(r"\+(\d{1,3})", value or "")
    return m.group(1) if m else ""


def valid_gmail(value):
    import re
    return bool(re.fullmatch(r"[^@\s]+@gmail\.com", value.lower()))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        require_csrf()
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip().lower()
        gmail = request.form.get("gmail", "").strip().lower()
        phone = normalize_phone(request.form.get("phone", ""))
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        file = request.files.get("photo")

        if not name or not username or not gmail or not phone or not password:
            flash("Name, username, Gmail, phone number and password are required.", "error")
            return render_template("register.html")
        if not valid_gmail(gmail):
            flash("Please enter a valid Gmail address ending in @gmail.com.", "error")
            return render_template("register.html")
        if not valid_phone(phone):
            flash("Please enter a valid international phone number with its country code.", "error")
            return render_template("register.html")
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("register.html")
        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("register.html")

        conn = get_db_connection()
        existing = conn.execute(
            "SELECT id FROM users WHERE lower(gmail) = ? OR lower(username) = ? OR phone = ?",
            (gmail, username, phone)
        ).fetchone()
        if existing:
            conn.close()
            flash("That Gmail, username or phone number is already registered.", "error")
            return render_template("register.html")

        filename = "default_profile.png"
        if file and file.filename:
            if not allowed_file(file.filename) or not validate_image_signature(file):
                conn.close()
                flash("Invalid profile image format.", "error")
                return render_template("register.html")
            filename = generate_unique_filename(file.filename, prefix="profile")
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

        cursor = conn.execute(
            "INSERT INTO users (name, gmail, phone, photo, username, password_hash) VALUES (?, ?, ?, ?, ?, ?)",
            (name, gmail, phone, filename, username, generate_password_hash(password))
        )
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        flash("Account created successfully. You can now log in with Gmail or phone number.", "success")
        return redirect(url_for("user_login"))

    return render_template("register.html")


# ---------------------------------------------------------
# Normal user authentication
# ---------------------------------------------------------

@app.before_request
def enforce_https():
    # TLS itself is provided by the production reverse proxy/web server.
    # This setting prevents accidental plaintext access once HTTPS is deployed.
    if REQUIRE_HTTPS and not request.is_secure:
        target = request.url.replace("http://", "https://", 1)
        return redirect(target, code=308)


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; img-src 'self' data: https://cdn.jsdelivr.net; media-src 'self'; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; connect-src 'self' https://ipapi.co")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


def user_required():
    return session.get("user_logged_in") is True and session.get("user_id") is not None


@app.route("/user-login", methods=["GET", "POST"])
def user_login():
    error = None
    if request.method == "POST":
        require_csrf()
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        phone_identifier = normalize_phone(identifier)
        conn = get_db_connection()
        user = conn.execute(
            "SELECT * FROM users WHERE lower(gmail) = ? OR lower(username) = ? OR phone = ?",
            (identifier.lower(), identifier.lower(), phone_identifier)
        ).fetchone()
        conn.close()
        if user and user["password_hash"] and check_password_hash(user["password_hash"], password):
            # Preserve an admin session if the same browser also has the admin
            # area open. The two authentication states use separate keys.
            session["user_logged_in"] = True
            session["user_id"] = user["id"]
            session.permanent = True
            session.modified = True
            return redirect(url_for("user_home"))
        error = "Invalid username, Gmail, phone number or password."
    return render_template("user_login.html", error=error)


@app.route("/user-logout")
def user_logout():
    session.pop("user_logged_in", None)
    session.pop("user_id", None)
    session.modified = True
    return redirect(url_for("user_login"))


@app.route("/user-home")
def user_home():
    if not user_required():
        return redirect(url_for("user_login"))

    user_id = session["user_id"]
    search = request.args.get("search", "").strip()

    conn = get_db_connection()

    current_user = conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not current_user:
        conn.close()
        session.pop("user_logged_in", None)
        session.pop("user_id", None)
        session.modified = True
        return redirect(url_for("user_login"))

    # People section: searchable so users do not need a separate page.
    uni_clause, uni_params = same_university_clause(current_user, "users")
    if search:
        users = conn.execute(
            f"""
            SELECT id, name, gmail, photo, username, nickname, relationship_status
            FROM users
            WHERE id != ? {uni_clause}
              AND (name LIKE ? OR username LIKE ? OR gmail LIKE ? OR nickname LIKE ?)
            ORDER BY name COLLATE NOCASE
            """,
            (user_id, *uni_params, f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%")
        ).fetchall()
    else:
        users = conn.execute(
            f"""SELECT id, name, gmail, photo, username, nickname, relationship_status
            FROM users WHERE id != ? {uni_clause} ORDER BY name COLLATE NOCASE""",
            (user_id, *uni_params)
        ).fetchall()

    unread_count = conn.execute(
        """
        SELECT COUNT(*) FROM messages
        WHERE receiver_id = ? AND is_read = 0
        """,
        (user_id,)
    ).fetchone()[0]

    total_members = conn.execute(
        f"SELECT COUNT(*) FROM users WHERE id != ? {uni_clause}",
        (user_id, *uni_params)
    ).fetchone()[0]

    conversation_count = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT CASE WHEN sender_id = ? THEN receiver_id ELSE sender_id END AS other_id
            FROM messages
            WHERE sender_id = ? OR receiver_id = ?
            GROUP BY other_id
        )
        """,
        (user_id, user_id, user_id)
    ).fetchone()[0]

    recent_conversations = conn.execute(
        """
        SELECT
            u.id, u.name, u.username, u.photo,
            (
                SELECT m.message FROM messages m
                WHERE (m.sender_id = ? AND m.receiver_id = u.id)
                   OR (m.sender_id = u.id AND m.receiver_id = ?)
                ORDER BY m.id DESC LIMIT 1
            ) AS last_message,
            (
                SELECT m.created_at FROM messages m
                WHERE (m.sender_id = ? AND m.receiver_id = u.id)
                   OR (m.sender_id = u.id AND m.receiver_id = ?)
                ORDER BY m.id DESC LIMIT 1
            ) AS last_message_at,
            (
                SELECT COUNT(*) FROM messages m
                WHERE m.sender_id = u.id AND m.receiver_id = ? AND m.is_read = 0
            ) AS unread_count
        FROM users u
        WHERE u.id != ?
          AND EXISTS (
              SELECT 1 FROM messages m
              WHERE (m.sender_id = ? AND m.receiver_id = u.id)
                 OR (m.sender_id = u.id AND m.receiver_id = ?)
          )
        ORDER BY last_message_at DESC
        LIMIT 5
        """,
        (user_id, user_id, user_id, user_id, user_id, user_id, user_id, user_id)
    ).fetchall()

    joined_groups = conn.execute("""SELECT g.id,g.name,(SELECT COUNT(*) FROM group_members gm2 WHERE gm2.group_id=g.id) member_count
        FROM groups g JOIN group_members gm ON gm.group_id=g.id WHERE gm.user_id=? ORDER BY g.name LIMIT 8""", (user_id,)).fetchall()
    joined_chat_groups = conn.execute("""SELECT cg.id,cg.name,(SELECT COUNT(*) FROM chat_group_members cm2 WHERE cm2.chat_group_id=cg.id) member_count
        FROM chat_groups cg JOIN chat_group_members cm ON cm.chat_group_id=cg.id WHERE cm.user_id=? ORDER BY cg.name LIMIT 8""", (user_id,)).fetchall()
    posts = fetch_feed(conn, user_id, 0, 8)
    feed_posts = serialize_posts(conn, posts, user_id)
    stories = serialize_stories(conn, fetch_stories(conn, user_id))
    conn.close()

    return render_template(
        "user_home.html",
        current_user=current_user,
        users=users,
        unread_count=unread_count,
        total_members=total_members,
        conversation_count=conversation_count,
        recent_conversations=recent_conversations,
        search=search,
        feed_posts=feed_posts,
        stories=stories,
        joined_groups=joined_groups,
        joined_chat_groups=joined_chat_groups,
        csrf=csrf_token()
    )


@app.route("/feed")
def feed():
    if not user_required():
        return redirect(url_for("user_login"))
    return redirect(url_for("user_home") + "#newsfeed")


def fetch_feed(conn, user_id, offset=0, limit=8):
    current = conn.execute("SELECT university_id FROM users WHERE id=?", (user_id,)).fetchone()
    uni_clause, uni_params = same_university_clause(current, "u")
    return conn.execute(
        f"""
        SELECT p.*, u.name, u.username, u.photo,
               (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id=p.id) AS like_count,
               (SELECT COUNT(*) FROM post_comments pc WHERE pc.post_id=p.id) AS comment_count,
               EXISTS(SELECT 1 FROM post_likes me WHERE me.post_id=p.id AND me.user_id=?) AS liked_by_me
        FROM posts p
        JOIN users u ON u.id=p.user_id
        WHERE 1=1 {uni_clause}
        ORDER BY p.id DESC
        LIMIT ? OFFSET ?
        """, (user_id, *uni_params, limit, offset)
    ).fetchall()


def fetch_reels(conn, user_id, offset=0, limit=12):
    current = conn.execute("SELECT university_id FROM users WHERE id=?", (user_id,)).fetchone()
    uni_clause, uni_params = same_university_clause(current, "u")
    return conn.execute(
        f"""
        SELECT p.*, u.name, u.username, u.photo,
               (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id=p.id) AS like_count,
               (SELECT COUNT(*) FROM post_comments pc WHERE pc.post_id=p.id) AS comment_count,
               EXISTS(SELECT 1 FROM post_likes me WHERE me.post_id=p.id AND me.user_id=?) AS liked_by_me
        FROM posts p JOIN users u ON u.id=p.user_id
        WHERE p.post_type='reel' {uni_clause}
        ORDER BY p.id DESC LIMIT ? OFFSET ?
        """, (user_id, *uni_params, limit, offset)
    ).fetchall()


@app.route("/reels")
def reels_page():
    if not user_required():
        return redirect(url_for("user_login"))
    conn = get_db_connection()
    posts = fetch_reels(conn, session["user_id"], 0, 20)
    reels = serialize_posts(conn, posts, session["user_id"])
    current_user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    conn.close()
    return render_template("reels.html", current_user=current_user, reels=reels, csrf=csrf_token(), site_name=get_site_name())


def fetch_user_posts(conn, profile_user_id, limit=20, offset=0):
    """Return only posts authored by the requested profile owner."""
    return conn.execute(
        """
        SELECT p.*, u.name, u.username, u.photo,
               (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id=p.id) AS like_count,
               (SELECT COUNT(*) FROM post_comments pc WHERE pc.post_id=p.id) AS comment_count,
               EXISTS(SELECT 1 FROM post_likes me WHERE me.post_id=p.id AND me.user_id=?) AS liked_by_me
        FROM posts p
        JOIN users u ON u.id=p.user_id
        WHERE p.user_id=?
        ORDER BY p.id DESC
        LIMIT ? OFFSET ?
        """, (session.get("user_id", 0), profile_user_id, limit, offset)
    ).fetchall()


def serialize_posts(conn, posts, user_id):
    output=[]
    for p in posts:
        comments=conn.execute(
            """SELECT pc.comment, pc.created_at, u.name, u.photo
               FROM post_comments pc JOIN users u ON u.id=pc.user_id
               WHERE pc.post_id=? ORDER BY pc.id DESC LIMIT 3""", (p["id"],)
        ).fetchall()
        output.append({
            "id": p["id"], "user_id": p["user_id"], "name": p["name"], "username": p["username"], "photo": p["photo"],
            "profile_url": url_for("public_profile", user_id=p["user_id"]),
            "caption": p["caption"], "media_type": p["media_type"], "post_type": p["post_type"] if "post_type" in p.keys() else "post",
            "media_url": (url_for("private_post_media", token=p["media_token"]) if p["original_name"] else None),
            "created_at": p["created_at"], "like_count": p["like_count"],
            "comment_count": p["comment_count"], "liked_by_me": bool(p["liked_by_me"]),
            "is_owner": int(p["user_id"]) == int(user_id),
            "comments":[dict(c) for c in comments]
        })
    return output


@app.route("/api/feed")
def api_feed():
    if not user_required():
        return jsonify({"error":"login_required"}), 401
    try:
        offset=max(0, int(request.args.get("offset", 0)))
    except ValueError:
        offset=0
    conn=get_db_connection()
    posts=fetch_feed(conn, session["user_id"], offset, 8)
    data=serialize_posts(conn, posts, session["user_id"])
    conn.close()
    return jsonify({"posts":data, "has_more":len(data)==8})


@app.route("/stories/create", methods=["POST"])
def create_story():
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", "")
    if not user_required():
        return (jsonify({"error": "login_required"}), 401) if wants_json else redirect(url_for("user_login"))
    require_csrf()
    media=request.files.get("story_media")
    caption=request.form.get("story_caption", "").strip()[:500]
    if not media or not media.filename:
        message="Choose a photo or short video for your story."
        if wants_json: return jsonify({"error": message}), 400
        flash(message, "error"); return redirect(url_for("user_home"))
    original=secure_filename(media.filename)
    ext=original.rsplit(".",1)[-1].lower() if "." in original else ""
    allowed=POST_IMAGE_EXTENSIONS|POST_VIDEO_EXTENSIONS
    if ext not in allowed:
        message="Unsupported story format. Use JPG, PNG, WEBP, GIF, MP4, WEBM, MOV or M4V."
        if wants_json: return jsonify({"error": message}), 400
        flash(message, "error"); return redirect(url_for("user_home"))
    media_type="video" if ext in POST_VIDEO_EXTENSIONS else "image"
    if not validate_media_signature(media, media_type):
        message="The uploaded file does not match its declared media type."
        if wants_json: return jsonify({"error": message}), 400
        flash(message, "error"); return redirect(url_for("user_home"))
    token=secrets.token_urlsafe(36)
    path=os.path.join(PRIVATE_MEDIA_FOLDER, token+"."+ext)
    try:
        media.save(path)
        now=datetime.utcnow().replace(microsecond=0)
        from datetime import timedelta
        expires=now+timedelta(hours=24)
        conn=get_db_connection()
        cursor=conn.execute("INSERT INTO stories(user_id,media_token,media_type,original_name,caption,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                     (session["user_id"], token, media_type, original, caption, now.strftime("%Y-%m-%d %H:%M:%S"), expires.strftime("%Y-%m-%d %H:%M:%S")))
        story_id=cursor.lastrowid
        conn.commit(); conn.close()
    except Exception as exc:
        try: os.remove(path)
        except OSError: pass
        app.logger.exception("Story upload failed")
        message="The story could not be uploaded. Please try the upload again."
        if wants_json: return jsonify({"error": message}), 500
        flash(message, "error"); return redirect(url_for("user_home"))
    if wants_json:
        return jsonify({"ok": True, "message": "Your story is live for 24 hours.", "story_id": story_id})
    flash("Your story is live for 24 hours.", "success")
    return redirect(url_for("user_home"))


def fetch_stories(conn, user_id):
    cleanup_expired_stories(conn)
    current = conn.execute("SELECT university_id FROM users WHERE id=?", (user_id,)).fetchone()
    uni_clause, uni_params = same_university_clause(current, "u")
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    return conn.execute(
        f"""SELECT s.*, u.name, u.username, u.photo,
                  CASE WHEN s.user_id=? THEN 1 ELSE 0 END AS is_mine
           FROM stories s JOIN users u ON u.id=s.user_id
           WHERE s.expires_at > ? {uni_clause}
           ORDER BY is_mine DESC, s.id DESC
           LIMIT 30""",
        (user_id, now, *uni_params)
    ).fetchall()


def serialize_stories(conn, stories):
    return [{
        "id": s["id"], "user_id": s["user_id"], "name": s["name"], "username": s["username"],
        "photo": s["photo"], "media_type": s["media_type"], "caption": s["caption"],
        "media_url": url_for("private_story_media", token=s["media_token"]),
        "profile_url": url_for("public_profile", user_id=s["user_id"]),
        "created_at": s["created_at"], "is_mine": bool(s["is_mine"])
    } for s in stories]


@app.route("/private-story-media/<token>")
def private_story_media(token):
    if not user_required(): abort(401)
    conn=get_db_connection()
    story=conn.execute("SELECT s.*, u.university_id FROM stories s JOIN users u ON u.id=s.user_id WHERE s.media_token=?", (token,)).fetchone()
    viewer=conn.execute("SELECT university_id FROM users WHERE id=?", (session["user_id"],)).fetchone()
    conn.close()
    if not story: abort(404)
    if story["expires_at"] <= datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"): abort(404)
    if story["university_id"] and viewer and viewer["university_id"] and int(story["university_id"]) != int(viewer["university_id"]): abort(403)
    ext=story["original_name"].rsplit(".",1)[-1].lower() if "." in story["original_name"] else ""
    if not re.fullmatch(r"[a-z0-9]{1,8}", ext): abort(404)
    path=os.path.join(PRIVATE_MEDIA_FOLDER, token+"."+ext)
    if not os.path.isfile(path): abort(404)
    return send_file(path, conditional=True, max_age=0)


@app.route("/stories/<int:story_id>/delete", methods=["POST"])
def delete_story(story_id):
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", "")
    if not user_required():
        return (jsonify({"error": "login_required"}), 401) if wants_json else redirect(url_for("user_login"))
    require_csrf()
    conn = get_db_connection()
    story = conn.execute("SELECT id, user_id, media_token, original_name FROM stories WHERE id=?", (story_id,)).fetchone()
    if not story:
        conn.close()
        return (jsonify({"error": "story_not_found"}), 404) if wants_json else redirect(url_for("user_home"))
    if int(story["user_id"]) != int(session["user_id"]):
        conn.close()
        return (jsonify({"error": "not_allowed"}), 403) if wants_json else redirect(url_for("user_home"))
    try:
        remove_private_media(story["media_token"], story["original_name"])
        conn.execute("DELETE FROM stories WHERE id=? AND user_id=?", (story_id, session["user_id"]))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        app.logger.exception("Story deletion failed")
        return (jsonify({"error": "story_delete_failed"}), 500) if wants_json else redirect(url_for("user_home"))
    conn.close()
    if wants_json:
        return jsonify({"ok": True, "story_id": story_id})
    flash("Story deleted.", "success")
    return redirect(url_for("user_home"))


@app.route("/api/stories")
def api_stories():
    if not user_required(): return jsonify({"error":"login_required"}),401
    conn=get_db_connection(); stories=fetch_stories(conn, session["user_id"]); data=serialize_stories(conn, stories); conn.close()
    return jsonify({"stories":data})


@app.route("/posts/create", methods=["POST"])
def create_post():
    if not user_required():
        return redirect(url_for("user_login"))
    require_csrf()
    caption=request.form.get("caption", "").strip()[:2000]
    media=request.files.get("media")
    mode=request.form.get("post_type", "post").strip().lower()
    has_media=bool(media and media.filename)
    if not caption and not has_media:
        flash("Write something or choose a photo/video before publishing.", "error")
        return redirect(url_for("user_home")+"#newsfeed")
    if mode not in {"post", "reel"}:
        mode="post"
    conn=get_db_connection()
    token=secrets.token_urlsafe(36)
    original=""
    media_type="image"
    saved_path=None
    try:
        if has_media:
            original=secure_filename(media.filename)
            ext=original.rsplit(".",1)[-1].lower() if "." in original else ""
            allowed=POST_VIDEO_EXTENSIONS if mode == "reel" else (POST_IMAGE_EXTENSIONS|POST_VIDEO_EXTENSIONS)
            if ext not in allowed:
                raise ValueError("Unsupported media format.")
            media_type="video" if ext in POST_VIDEO_EXTENSIONS else "image"
            if mode == "reel" and media_type != "video":
                raise ValueError("A Reel must be a video.")
            if not validate_media_signature(media, media_type):
                raise ValueError("The uploaded file does not match its declared media type.")
            saved_path=os.path.join(PRIVATE_MEDIA_FOLDER, token+"."+ext)
            media.save(saved_path)
        elif mode == "reel":
            raise ValueError("Choose a video for your Reel.")
        post_type = "reel" if mode == "reel" else "post"
        cursor=conn.execute("INSERT INTO posts(user_id,caption,media_token,media_type,original_name,post_type,created_at) VALUES(?,?,?,?,?,?,?)",
                     (session["user_id"], caption, token, media_type, original, post_type, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
        post_id=cursor.lastrowid
        conn.commit()
    except ValueError as exc:
        conn.rollback(); conn.close()
        if saved_path:
            try: os.remove(saved_path)
            except OSError: pass
        flash(str(exc), "error")
        return redirect(url_for("user_home")+"#newsfeed")
    except Exception:
        conn.rollback(); conn.close()
        if saved_path:
            try: os.remove(saved_path)
            except OSError: pass
        app.logger.exception("Post creation failed")
        flash("The post could not be published. Please try again.", "error")
        return redirect(url_for("user_home")+"#newsfeed")
    conn.close()
    flash("Reel published." if mode == "reel" else "Post published to the student newsfeed.", "success")
    return redirect(url_for("user_home")+"#newsfeed")


@app.route("/post/<int:post_id>/like", methods=["POST"])
def toggle_post_like(post_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    require_csrf()
    conn=get_db_connection(); uid=session["user_id"]
    post=conn.execute("SELECT p.id,u.university_id FROM posts p JOIN users u ON u.id=p.user_id WHERE p.id=?",(post_id,)).fetchone()
    viewer=conn.execute("SELECT university_id FROM users WHERE id=?",(uid,)).fetchone()
    if not post: conn.close(); return jsonify({"error":"Post not found"}),404
    if post["university_id"] and viewer and viewer["university_id"] and int(post["university_id"]) != int(viewer["university_id"]): conn.close(); return jsonify({"error":"You cannot react to this post."}),403
    exists=conn.execute("SELECT 1 FROM post_likes WHERE post_id=? AND user_id=?",(post_id,uid)).fetchone()
    if exists: conn.execute("DELETE FROM post_likes WHERE post_id=? AND user_id=?",(post_id,uid)); liked=False
    else: conn.execute("INSERT INTO post_likes(post_id,user_id,created_at) VALUES(?,?,?)",(post_id,uid,datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))); liked=True
    count=conn.execute("SELECT COUNT(*) FROM post_likes WHERE post_id=?",(post_id,)).fetchone()[0]
    conn.commit(); conn.close()
    return jsonify({"liked":liked,"like_count":count})


@app.route("/post/<int:post_id>/comment", methods=["POST"])
def add_post_comment(post_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    require_csrf(); comment=request.form.get("comment","").strip()
    if not comment or len(comment)>1000: return jsonify({"error":"Invalid comment"}),400
    conn=get_db_connection(); uid=session["user_id"]
    exists=conn.execute("SELECT p.id,u.university_id FROM posts p JOIN users u ON u.id=p.user_id WHERE p.id=?",(post_id,)).fetchone()
    viewer=conn.execute("SELECT university_id FROM users WHERE id=?",(uid,)).fetchone()
    if not exists: conn.close(); return jsonify({"error":"Post not found"}),404
    if exists["university_id"] and viewer and viewer["university_id"] and int(exists["university_id"]) != int(viewer["university_id"]): conn.close(); return jsonify({"error":"You cannot comment on this post."}),403
    conn.execute("INSERT INTO post_comments(post_id,user_id,comment,created_at) VALUES(?,?,?,?)",(post_id,uid,comment,datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
    count=conn.execute("SELECT COUNT(*) FROM post_comments WHERE post_id=?",(post_id,)).fetchone()[0]
    conn.commit(); conn.close()
    return jsonify({"comment_count":count})


@app.route("/post/<int:post_id>/edit", methods=["POST"])
def edit_post(post_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    require_csrf(); uid=session["user_id"]
    caption=request.form.get("caption","").strip()[:2000]
    conn=get_db_connection(); post=conn.execute("SELECT * FROM posts WHERE id=?",(post_id,)).fetchone()
    if not post: conn.close(); return jsonify({"error":"Post not found."}),404
    if int(post["user_id"]) != int(uid): conn.close(); return jsonify({"error":"You can only edit your own posts."}),403
    if not caption and not post["original_name"]:
        conn.close(); return jsonify({"error":"A text post cannot be empty."}),400
    conn.execute("UPDATE posts SET caption=? WHERE id=?",(caption,post_id)); conn.commit(); conn.close()
    return jsonify({"ok":True,"post_id":post_id,"caption":caption})


@app.route("/post/<int:post_id>/delete", methods=["POST"])
def delete_post(post_id):
    if not user_required():
        return jsonify({"error": "login_required"}), 401
    require_csrf()
    uid=session["user_id"]
    conn=get_db_connection()
    post=conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not post:
        conn.close(); return jsonify({"error":"Post not found."}), 404
    if int(post["user_id"]) != int(uid):
        conn.close(); return jsonify({"error":"You can only delete your own posts."}), 403
    conn.execute("DELETE FROM post_likes WHERE post_id=?", (post_id,))
    conn.execute("DELETE FROM post_comments WHERE post_id=?", (post_id,))
    conn.execute("DELETE FROM posts WHERE id=?", (post_id,))
    conn.commit(); conn.close()
    remove_private_media(post["media_token"], post["original_name"])
    return jsonify({"ok":True,"post_id":post_id})


@app.route("/private-post-media/<token>")
def private_post_media(token):
    if not user_required(): abort(401)
    conn=get_db_connection()
    post=conn.execute("SELECT p.*, u.university_id FROM posts p JOIN users u ON u.id=p.user_id WHERE p.media_token=?",(token,)).fetchone()
    viewer=conn.execute("SELECT university_id FROM users WHERE id=?",(session["user_id"],)).fetchone()
    conn.close()
    if not post: abort(404)
    if post["university_id"] and viewer and viewer["university_id"] and int(post["university_id"]) != int(viewer["university_id"]): abort(403)
    if not post["original_name"]: abort(404)
    ext=post["original_name"].rsplit(".",1)[-1].lower() if "." in post["original_name"] else ""
    if not re.fullmatch(r"[a-z0-9]{1,8}", ext): abort(404)
    path=os.path.join(PRIVATE_MEDIA_FOLDER, token+"."+ext)
    if not os.path.isfile(path): abort(404)
    return send_file(path, conditional=True, max_age=0)


@app.route("/my-profile")
def my_profile():
    if not user_required():
        return redirect(url_for("user_login"))

    conn = get_db_connection()
    profile = conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()
    gallery = conn.execute(
        "SELECT * FROM gallery WHERE user_id = ? ORDER BY id DESC",
        (session["user_id"],)
    ).fetchall()
    profile_posts = serialize_posts(conn, fetch_user_posts(conn, session["user_id"], 20, 0), session["user_id"])
    conn.close()

    if not profile:
        session.pop("user_logged_in", None)
        session.pop("user_id", None)
        session.modified = True
        return redirect(url_for("user_login"))

    stored_view = profile["profile_view"] if "profile_view" in profile.keys() and profile["profile_view"] in ("facebook", "cv") else "facebook"
    requested_view = request.args.get("view", "").strip().lower()
    view = requested_view if requested_view in ("facebook", "cv") else stored_view
    return render_template("premium_cv_profile.html" if view == "cv" else "facebook_profile.html", profile=profile, gallery=gallery, profile_posts=profile_posts, is_own=True)


def create_media_update_post(conn, user_id, source_path, caption):
    """Create a historical feed post with its own private copy of an image."""
    if not source_path or not os.path.isfile(source_path):
        return
    ext=Path(source_path).suffix.lower().lstrip(".") or "jpg"
    if ext not in POST_IMAGE_EXTENSIONS:
        ext="jpg"
    token=secrets.token_urlsafe(36)
    dest=os.path.join(PRIVATE_MEDIA_FOLDER, token+"."+ext)
    import shutil
    shutil.copy2(source_path, dest)
    conn.execute("INSERT INTO posts(user_id,caption,media_token,media_type,original_name,post_type,created_at) VALUES(?,?,?,?,?,?,?)",
                 (user_id, caption, token, "image", "profile-update."+ext, "post", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))

@app.route("/profile/cover", methods=["POST"])
def update_cover_photo():
    if not user_required(): return redirect(url_for("user_login"))
    require_csrf(); uid=session["user_id"]
    media=request.files.get("cover_photo")
    if not media or not media.filename:
        flash("Choose a cover photo.", "error"); return redirect(url_for("my_profile"))
    if not allowed_file(media.filename) or not validate_image_signature(media):
        flash("Invalid cover photo format.", "error"); return redirect(url_for("my_profile"))
    filename=generate_unique_filename(media.filename, prefix="cover")
    path=os.path.join(app.config["UPLOAD_FOLDER"], filename); media.save(path)
    conn=get_db_connection(); old=conn.execute("SELECT cover_photo FROM users WHERE id=?",(uid,)).fetchone()
    try:
        conn.execute("UPDATE users SET cover_photo=? WHERE id=?",(filename,uid))
        create_media_update_post(conn, uid, path, "updated their cover photo.")
        conn.commit()
    except Exception:
        conn.rollback(); conn.close()
        try: os.remove(path)
        except OSError: pass
        app.logger.exception("Cover photo update failed")
        flash("The cover photo could not be updated. Please try again.","error")
        return redirect(url_for("my_profile"))
    conn.close()
    if old and old["cover_photo"] and old["cover_photo"] != filename:
        try: os.remove(os.path.join(app.config["UPLOAD_FOLDER"], old["cover_photo"]))
        except OSError: pass
    flash("Cover photo updated and shared to the newsfeed.", "success")
    return redirect(url_for("my_profile"))

# ---------------------------------------------------------
# User profile settings / CV profile
# ---------------------------------------------------------

@app.route("/profile-settings", methods=["GET", "POST"])
def profile_settings():
    if not user_required():
        return redirect(url_for("user_login"))

    user_id = session["user_id"]
    conn = get_db_connection()
    profile = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    if not profile:
        conn.close()
        session.pop("user_logged_in", None)
        session.pop("user_id", None)
        session.modified = True
        return redirect(url_for("user_login"))

    if request.method == "POST":
        require_csrf()
        form_type = request.form.get("form_type", "profile")

        if form_type == "profile":
            name = request.form.get("name", "").strip()
            username = request.form.get("username", "").strip().lower()
            gmail = request.form.get("gmail", "").strip().lower()
            nickname = request.form.get("nickname", "").strip()
            age = request.form.get("age", "").strip()
            relationship_status = request.form.get("relationship_status", "Single").strip()
            partner = request.form.get("partner", "").strip()
            phone = normalize_phone(request.form.get("phone", ""))
            if not valid_gmail(gmail):
                flash("Please enter a valid Gmail address ending in @gmail.com.", "error")
                conn.close()
                return redirect(url_for("profile_settings"))
            if not phone or not valid_phone(phone):
                flash("Please enter a valid international phone number with country code.", "error")
                conn.close()
                return redirect(url_for("profile_settings"))
            location = request.form.get("location", "").strip()
            birth_date = request.form.get("birth_date", "").strip()
            headline = request.form.get("headline", "").strip()
            occupation = request.form.get("occupation", "").strip()
            company = request.form.get("company", "").strip()
            website = request.form.get("website", "").strip()
            bio = request.form.get("bio", "").strip()
            education = request.form.get("education", "").strip()
            skills = request.form.get("skills", "").strip()
            experience = request.form.get("experience", "").strip()
            achievements = request.form.get("achievements", "").strip()
            interests = request.form.get("interests", "").strip()
            projects = request.form.get("projects", "").strip()
            certifications = request.form.get("certifications", "").strip()
            references_text = request.form.get("references_text", "").strip()
            career_objective = request.form.get("career_objective", "").strip()
            study_status = request.form.get("study_status", "").strip()
            department = request.form.get("department", "").strip()
            academic_year = request.form.get("academic_year", "").strip()
            semester = request.form.get("semester", "").strip()
            student_id = request.form.get("student_id", "").strip()
            university = request.form.get("university", "").strip()
            photo = request.files.get("photo")
            profile_view = request.form.get("profile_view", "facebook").strip().lower()
            if profile_view not in ("facebook", "cv"):
                profile_view = "facebook"

            if not name or not username or not gmail:
                flash("Name, username and Gmail are required.", "error")
                conn.close()
                return redirect(url_for("profile_settings"))

            duplicate = conn.execute(
                "SELECT id FROM users WHERE (lower(gmail) = ? OR lower(username) = ? OR phone = ?) AND id != ?",
                (gmail, username, phone, user_id)
            ).fetchone()
            if duplicate:
                flash("That Gmail or username is already in use.", "error")
                conn.close()
                return redirect(url_for("profile_settings"))

            filename = profile["photo"]
            profile_photo_changed = False
            profile_photo_path = None
            if photo and photo.filename:
                if not allowed_file(photo.filename) or not validate_image_signature(photo):
                    flash("Invalid profile image format.", "error")
                    conn.close()
                    return redirect(url_for("profile_settings"))
                new_filename = generate_unique_filename(photo.filename, prefix="profile")
                if new_filename:
                    profile_photo_path = os.path.join(app.config["UPLOAD_FOLDER"], new_filename)
                    photo.save(profile_photo_path)
                    filename = new_filename
                    profile_photo_changed = True

            try:
                conn.execute(
                    """
                    UPDATE users SET
                        name=?, gmail=?, username=?, photo=?, nickname=?, age=?,
                        relationship_status=?, partner=?, phone=?, location=?, birth_date=?,
                        headline=?, occupation=?, company=?, website=?, bio=?, education=?,
                        skills=?, experience=?, achievements=?, interests=?, projects=?, certifications=?, references_text=?, career_objective=?,
                        study_status=?, department=?, academic_year=?, semester=?, student_id=?, university=?, profile_view=?
                    WHERE id=?
                    """,
                    (name, gmail, username, filename, nickname, age or None,
                     relationship_status or "Single", partner, phone, location, birth_date,
                     headline, occupation, company, website, bio, education, skills,
                     experience, achievements, interests, projects, certifications, references_text, career_objective, study_status, department, academic_year, semester, student_id, university, profile_view, user_id)
                )
                if profile_photo_changed and profile_photo_path:
                    create_media_update_post(conn, user_id, profile_photo_path, "updated their profile photo.")
                conn.commit()
            except Exception:
                conn.rollback()
                if profile_photo_changed and profile_photo_path:
                    try: os.remove(profile_photo_path)
                    except OSError: pass
                app.logger.exception("Profile update failed")
                flash("Your profile could not be saved. Please try again.", "error")
                conn.close()
                return redirect(url_for("profile_settings"))
            # Remove previous profile image only after the historical post has its own copy.
            if profile_photo_changed and profile["photo"] and profile["photo"] != "default_profile.png":
                try: os.remove(os.path.join(app.config["UPLOAD_FOLDER"], profile["photo"]))
                except OSError: pass
            flash("Your profile and CV information have been saved.", "success")

        elif form_type == "password":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")
            if not profile["password_hash"] or not check_password_hash(profile["password_hash"], current_password):
                flash("Current password is incorrect.", "error")
            elif len(new_password) < 6:
                flash("New password must be at least 6 characters.", "error")
            elif new_password != confirm_password:
                flash("New passwords do not match.", "error")
            else:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_password), user_id))
                conn.commit()
                flash("Password changed successfully.", "success")

        conn.close()
        return redirect(url_for("profile_settings"))

    conn.close()
    return render_template("profile_settings.html", profile=profile)


@app.route("/profile/<int:user_id>")
def public_profile(user_id):
    if not user_required():
        return redirect(url_for("user_login"))

    conn = get_db_connection()
    profile = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    gallery = conn.execute(
        "SELECT * FROM gallery WHERE user_id = ? ORDER BY id DESC",
        (user_id,)
    ).fetchall()
    profile_posts = serialize_posts(conn, fetch_user_posts(conn, user_id, 20, 0), session["user_id"])
    conn.close()

    if not profile:
        return "<h1>Profile not found</h1>", 404

    view = profile["profile_view"] if "profile_view" in profile.keys() and profile["profile_view"] in ("facebook", "cv") else "facebook"
    template = "premium_cv_profile.html" if view == "cv" else "facebook_profile.html"
    return render_template(template, profile=profile, gallery=gallery, profile_posts=profile_posts, is_own=(user_id == session["user_id"]))


# ---------------------------------------------------------
# Appearance, chat preferences and cryptographic account tools
# ---------------------------------------------------------

@app.route('/settings/appearance', methods=['GET', 'POST'])
def appearance_settings():
    if not user_required(): return redirect(url_for('user_login'))
    uid=session['user_id']
    conn=get_db_connection()
    if request.method=='POST':
        require_csrf()
        theme=request.form.get('theme','light')
        if theme not in {'light','dark','system'}: theme='light'
        session['theme']=theme
        conn.close()
        flash('Appearance updated.', 'success')
        return redirect(url_for('appearance_settings'))
    conn.close()
    return render_template('appearance_settings.html', theme=session.get('theme','light'))

@app.route('/api/chat/<int:user_id>/preferences', methods=['GET','POST'])
def chat_preferences(user_id):
    if not user_required(): return jsonify({'error':'login_required'}),401
    if user_id==session['user_id']: return jsonify({'error':'invalid_peer'}),400
    conn=get_db_connection(); now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if not conn.execute('SELECT id FROM users WHERE id=?',(user_id,)).fetchone():
        conn.close(); return jsonify({'error':'user_not_found'}),404
    if request.method=='POST':
        require_csrf(); data=request.get_json(silent=True) or {}
        theme=data.get('theme','default'); wallpaper=data.get('wallpaper','none')
        try: disappear=max(0,min(int(data.get('disappearing_seconds',0)),604800))
        except Exception: disappear=0
        e2ee=1 if bool(data.get('e2ee_enabled',False)) else 0
        if theme not in {'default','dark','light','midnight','forest'}: theme='default'
        if wallpaper not in {'none','dots','gradient','paper','night'}: wallpaper='none'
        if e2ee:
            own_key=get_public_key(conn,session['user_id'])
            if not own_key:
                conn.close(); return jsonify({'error':'Your browser has not created a secure key yet. Turn on E2EE again to initialize it.','requires_keys':True}),409
        row=conn.execute('SELECT user_id FROM chat_preferences WHERE user_id=? AND peer_id=?',(session['user_id'],user_id)).fetchone()
        if row:
            conn.execute('UPDATE chat_preferences SET theme=?,wallpaper=?,disappearing_seconds=?,e2ee_enabled=?,updated_at=? WHERE user_id=? AND peer_id=?',(theme,wallpaper,disappear,e2ee,now,session['user_id'],user_id))
        else:
            conn.execute('INSERT INTO chat_preferences(user_id,peer_id,theme,wallpaper,disappearing_seconds,e2ee_enabled,updated_at) VALUES(?,?,?,?,?,?,?)',(session['user_id'],user_id,theme,wallpaper,disappear,e2ee,now))
        conn.commit(); conn.close(); return jsonify({'ok':True,'theme':theme,'wallpaper':wallpaper,'disappearing_seconds':disappear,'e2ee_enabled':bool(e2ee)})
    row=conn.execute('SELECT theme,wallpaper,disappearing_seconds,e2ee_enabled FROM chat_preferences WHERE user_id=? AND peer_id=?',(session['user_id'],user_id)).fetchone()
    peer_pref=conn.execute('SELECT e2ee_enabled FROM chat_preferences WHERE user_id=? AND peer_id=?',(user_id,session['user_id'])).fetchone()
    conn.close()
    return jsonify(dict(row) if row else {'theme':'default','wallpaper':'none','disappearing_seconds':0,'e2ee_enabled':False,'peer_e2ee_enabled':bool(peer_pref and peer_pref['e2ee_enabled'])})

@app.route('/api/e2ee/safety-number/<int:user_id>')
def safety_number(user_id):
    if not user_required(): return jsonify({'error':'login_required'}),401
    import hashlib
    conn=get_db_connection(); a=get_public_key(conn,session['user_id']); b=get_public_key(conn,user_id); conn.close()
    if not a or not b: return jsonify({'error':'keys_not_ready'}),404
    material='|'.join(sorted([a,b])).encode(); digest=hashlib.sha256(material).hexdigest()
    grouped=' '.join(digest[i:i+5] for i in range(0,40,5))
    return jsonify({'safety_number':grouped,'fingerprint':digest})

@app.route('/api/e2ee/device', methods=['POST'])
def register_device():
    if not user_required(): return jsonify({'error':'login_required'}),401
    require_csrf(); data=request.get_json(silent=True) or {}; uid=session['user_id']
    device_id=str(data.get('device_id','')).strip(); identity=str(data.get('identity_public_key','')).strip()
    if not re.fullmatch(r'[A-Za-z0-9._:-]{8,128}', device_id) or len(identity)<40 or len(identity)>5000:
        return jsonify({'error':'invalid_device'}),400
    now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'); conn=get_db_connection()
    device_name=str(data.get('device_name','Browser'))[:80]
    existing=conn.execute('SELECT user_id FROM user_devices WHERE device_id=?',(device_id,)).fetchone()
    if existing and int(existing['user_id']) != int(uid):
        conn.close(); return jsonify({'error':'device_id_already_registered'}),409
    if existing:
        conn.execute('UPDATE user_devices SET identity_public_key=?,device_name=?,signed_prekey=?,one_time_prekey=?,last_seen_at=?,revoked=0 WHERE device_id=? AND user_id=?',
                     (identity,device_name,data.get('signed_prekey'),data.get('one_time_prekey'),now,device_id,uid))
    else:
        conn.execute('INSERT INTO user_devices(user_id,device_id,device_name,identity_public_key,signed_prekey,one_time_prekey,created_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?)',
                     (uid,device_id,device_name,identity,data.get('signed_prekey'),data.get('one_time_prekey'),now,now))
    conn.commit(); conn.close(); return jsonify({'ok':True,'device_id':device_id})


@app.route('/api/e2ee/devices')
def devices():
    if not user_required(): return jsonify({'error':'login_required'}),401
    conn=get_db_connection(); rows=conn.execute('SELECT device_id,device_name,created_at,last_seen_at,revoked FROM user_devices WHERE user_id=? ORDER BY last_seen_at DESC',(session['user_id'],)).fetchall(); conn.close(); return jsonify({'devices':[dict(x) for x in rows]})

@app.route('/api/e2ee/backup', methods=['GET','POST','DELETE'])
def encrypted_backup():
    if not user_required(): return jsonify({'error':'login_required'}),401
    uid=session['user_id']; conn=get_db_connection()
    if request.method=='GET':
        row=conn.execute('SELECT version,salt,iv,ciphertext,updated_at FROM encrypted_key_backups WHERE user_id=?',(uid,)).fetchone(); conn.close()
        return jsonify(dict(row) if row else {'backup':None})
    require_csrf()
    if request.method=='DELETE':
        conn.execute('DELETE FROM encrypted_key_backups WHERE user_id=?',(uid,)); conn.commit(); conn.close(); return jsonify({'ok':True})
    data=request.get_json(silent=True) or {}; salt=str(data.get('salt','')); iv=str(data.get('iv','')); ciphertext=str(data.get('ciphertext',''))
    if not salt or not iv or not ciphertext or len(ciphertext)>5000000: conn.close(); return jsonify({'error':'invalid_backup'}),400
    now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'); row=conn.execute('SELECT user_id FROM encrypted_key_backups WHERE user_id=?',(uid,)).fetchone()
    if row: conn.execute('UPDATE encrypted_key_backups SET salt=?,iv=?,ciphertext=?,updated_at=? WHERE user_id=?',(salt,iv,ciphertext,now,uid))
    else: conn.execute('INSERT INTO encrypted_key_backups(user_id,salt,iv,ciphertext,created_at,updated_at) VALUES(?,?,?,?,?,?)',(uid,salt,iv,ciphertext,now,now))
    conn.commit(); conn.close(); return jsonify({'ok':True})

# ---------------------------------------------------------
# Private messaging
# ---------------------------------------------------------

@app.route("/messages")
def messages():
    if not user_required():
        return redirect(url_for("user_login"))

    current_user_id = session["user_id"]
    conn = get_db_connection()

    current_user = conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (current_user_id,)
    ).fetchone()

    conversations = conn.execute(
        """
        SELECT
            u.id,
            u.name,
            u.username,
            u.photo,
            (
                SELECT m.message
                FROM messages m
                WHERE (m.sender_id = ? AND m.receiver_id = u.id)
                   OR (m.sender_id = u.id AND m.receiver_id = ?)
                ORDER BY m.id DESC LIMIT 1
            ) AS last_message,
            (
                SELECT m.created_at
                FROM messages m
                WHERE (m.sender_id = ? AND m.receiver_id = u.id)
                   OR (m.sender_id = u.id AND m.receiver_id = ?)
                ORDER BY m.id DESC LIMIT 1
            ) AS last_message_at,
            (
                SELECT COUNT(*)
                FROM messages m
                WHERE m.sender_id = u.id
                  AND m.receiver_id = ?
                  AND m.is_read = 0
            ) AS unread_count
        FROM users u
        WHERE u.id != ?
          AND EXISTS (
              SELECT 1 FROM messages m
              WHERE (m.sender_id = ? AND m.receiver_id = u.id)
                 OR (m.sender_id = u.id AND m.receiver_id = ?)
          )
        ORDER BY COALESCE(last_message_at, '') DESC, u.name COLLATE NOCASE
        """,
        (
            current_user_id, current_user_id,
            current_user_id, current_user_id,
            current_user_id,
            current_user_id,
            current_user_id, current_user_id
        )
    ).fetchall()

    users = conn.execute(
        """
        SELECT id, name, username, photo
        FROM users
        WHERE id != ?
        ORDER BY name COLLATE NOCASE
        """,
        (current_user_id,)
    ).fetchall()
    chat_groups = conn.execute("""SELECT cg.*, (SELECT COUNT(*) FROM chat_group_members cm WHERE cm.chat_group_id=cg.id) AS member_count
        FROM chat_groups cg JOIN chat_group_members mine ON mine.chat_group_id=cg.id AND mine.user_id=? ORDER BY cg.id DESC LIMIT 12""",(current_user_id,)).fetchall()
    social_groups = conn.execute("SELECT g.id,g.name FROM groups g JOIN group_members gm ON gm.group_id=g.id WHERE gm.user_id=? ORDER BY g.name",(current_user_id,)).fetchall()

    conn.close()

    return render_template(
        "messages.html",
        current_user=current_user,
        conversations=conversations,
        users=users,
        chat_groups=chat_groups,
        social_groups=social_groups
    )


def users_are_blocked(conn, user_a, user_b):
    return bool(conn.execute(
        "SELECT 1 FROM user_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?) LIMIT 1",
        (user_a, user_b, user_b, user_a)
    ).fetchone())


def get_public_key(conn, user_id):
    row = conn.execute("SELECT public_key FROM user_keys WHERE user_id=?", (user_id,)).fetchone()
    return row["public_key"] if row else None


@app.route("/messages/<int:user_id>")
def chat(user_id):
    if not user_required():
        return redirect(url_for("user_login"))
    current_user_id = session["user_id"]
    if user_id == current_user_id:
        return redirect(url_for("messages"))
    conn = get_db_connection()
    other_user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not other_user:
        conn.close(); return "<h1>User not found</h1>", 404
    blocked = users_are_blocked(conn, current_user_id, user_id)
    now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at<=?", (now,))
    if not blocked:
        conn.execute("""UPDATE messages SET delivered_at=COALESCE(delivered_at, ?), is_read=1, read_at=COALESCE(read_at, ?) WHERE sender_id=? AND receiver_id=?""",
                     (now, now, user_id, current_user_id))
    chat_messages = conn.execute("""
        SELECT m.*, sender.name AS sender_name FROM messages m
        JOIN users sender ON sender.id=m.sender_id
        WHERE ((m.sender_id=? AND m.receiver_id=?) OR (m.sender_id=? AND m.receiver_id=?))
          AND (m.expires_at IS NULL OR m.expires_at > ?)
        ORDER BY m.id ASC
    """, (current_user_id,user_id,user_id,current_user_id,now)).fetchall()
    current_user = conn.execute("SELECT * FROM users WHERE id=?", (current_user_id,)).fetchone()
    other_public_key = get_public_key(conn, user_id)
    my_public_key = get_public_key(conn, current_user_id)
    pref = conn.execute('SELECT theme,wallpaper,disappearing_seconds,e2ee_enabled FROM chat_preferences WHERE user_id=? AND peer_id=?',(current_user_id,user_id)).fetchone()
    peer_pref = conn.execute('SELECT e2ee_enabled FROM chat_preferences WHERE user_id=? AND peer_id=?',(user_id,current_user_id)).fetchone()
    conn.commit(); conn.close()
    response = make_response(render_template("chat.html", current_user=current_user, other_user=other_user,
                           chat_messages=chat_messages, blocked=blocked,
                           my_public_key=my_public_key, other_public_key=other_public_key, chat_pref=(dict(pref) if pref else {'theme':'default','wallpaper':'none','disappearing_seconds':0,'e2ee_enabled':0}), peer_e2ee_enabled=bool(peer_pref and peer_pref['e2ee_enabled'])))
    # Chat pages must not be served from a stale browser cache. This is especially
    # important after removing the legacy voice/video-call JavaScript.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/api/e2ee/key", methods=["POST"])
def save_e2ee_key():
    if not user_required(): return jsonify({"error":"login_required"}), 401
    require_csrf()
    public_key=(request.get_json(silent=True) or {}).get("public_key", "").strip()
    if len(public_key) < 40 or len(public_key) > 5000:
        return jsonify({"error":"invalid_public_key"}), 400
    uid=session["user_id"]; now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn=get_db_connection()
    existing=conn.execute("SELECT user_id FROM user_keys WHERE user_id=?",(uid,)).fetchone()
    if existing:
        conn.execute("UPDATE user_keys SET public_key=?, updated_at=? WHERE user_id=?",(public_key,now,uid))
    else:
        conn.execute("INSERT INTO user_keys(user_id,public_key,created_at,updated_at) VALUES(?,?,?,?)",(uid,public_key,now,now))
    conn.commit(); conn.close()
    return jsonify({"ok":True,"public_key":public_key})


@app.route("/api/e2ee/key/<int:user_id>")
def get_e2ee_key(user_id):
    if not user_required(): return jsonify({"error":"login_required"}), 401
    conn=get_db_connection(); key=get_public_key(conn,user_id); conn.close()
    # A peer may simply not have opened secure chat on this device yet.
    # Return a normal JSON response instead of a noisy 404; the browser client
    # already treats a missing key as a not-ready state.
    if not key: return jsonify({"public_key": None, "ready": False})
    return jsonify({"public_key":key, "ready": True})


@app.route("/messages/send", methods=["POST"])
def send_message():
    if not user_required():
        if request.headers.get("X-Requested-With")=="XMLHttpRequest": return jsonify({"error":"login_required"}),401
        return redirect(url_for("user_login"))
    require_csrf(); sender_id=session["user_id"]
    receiver_id=request.form.get("receiver_id",type=int)
    ciphertext=request.form.get("ciphertext","").strip()
    iv=request.form.get("iv","").strip()
    message_text=request.form.get("message","").strip()
    if not receiver_id or receiver_id==sender_id:
        return jsonify({"error":"Invalid recipient."}),400
    conn=get_db_connection()
    recipient=conn.execute("SELECT id FROM users WHERE id=?",(receiver_id,)).fetchone()
    if not recipient: conn.close(); return jsonify({"error":"Recipient not found."}),404
    if users_are_blocked(conn,sender_id,receiver_id): conn.close(); return jsonify({"error":"You cannot message this user because one of you has blocked the other."}),403
    pref=conn.execute("SELECT e2ee_enabled,disappearing_seconds FROM chat_preferences WHERE user_id=? AND peer_id=?",(sender_id,receiver_id)).fetchone()
    secure=bool(pref and pref["e2ee_enabled"])
    if secure:
        if not ciphertext or not iv:
            conn.close(); return jsonify({"error":"Secure chat is enabled. Your browser could not encrypt this message."}),400
        if len(ciphertext)>20000 or len(iv)>100:
            conn.close(); return jsonify({"error":"Encrypted payload is too large."}),400
        stored_message="[Encrypted message]"; encryption_version=1
    else:
        if not message_text:
            conn.close(); return jsonify({"error":"Message cannot be empty."}),400
        if len(message_text)>5000:
            conn.close(); return jsonify({"error":"Message is too long."}),400
        ciphertext=None; iv=None; stored_message=message_text; encryption_version=0
    created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    disappear=int(pref["disappearing_seconds"] if pref else 0)
    expires_at=None
    if disappear>0:
        from datetime import timedelta
        expires_at=(datetime.now()+timedelta(seconds=disappear)).strftime("%Y-%m-%d %H:%M:%S")
    cursor=conn.execute("""INSERT INTO messages(sender_id,receiver_id,message,ciphertext,iv,encryption_version,created_at,expires_at,is_read,delivered_at,read_at) VALUES(?,?,?,?,?,?,?,?,0,NULL,NULL)""",
                        (sender_id,receiver_id,stored_message,ciphertext,iv,encryption_version,created_at,expires_at))
    message_id=cursor.lastrowid; conn.commit(); conn.close()
    return jsonify({"ok":True,"message":{"id":message_id,"sender_id":sender_id,"receiver_id":receiver_id,
        "message":stored_message,"ciphertext":ciphertext,"iv":iv,"encryption_version":encryption_version,
        "created_at":created_at,"expires_at":expires_at,"is_read":0,"delivered_at":None}})


@app.route("/api/messages/<int:user_id>")
def api_messages(user_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    current_user_id=session["user_id"]
    if user_id==current_user_id: return jsonify({"error":"invalid_recipient"}),400
    try: since_id=max(0,int(request.args.get("since_id",0)))
    except (TypeError,ValueError): since_id=0
    conn=get_db_connection()
    if not conn.execute("SELECT id FROM users WHERE id=?",(user_id,)).fetchone(): conn.close(); return jsonify({"error":"user_not_found"}),404
    blocked=users_are_blocked(conn,current_user_id,user_id)
    now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at<=?",(now,))
    if not blocked:
        conn.execute("UPDATE messages SET delivered_at=COALESCE(delivered_at,?), is_read=1, read_at=COALESCE(read_at,?) WHERE sender_id=? AND receiver_id=?",(now,now,user_id,current_user_id))
    rows=conn.execute("""SELECT id,sender_id,receiver_id,message,ciphertext,iv,encryption_version,created_at,expires_at,is_read,delivered_at,read_at FROM messages WHERE id>? AND ((sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)) ORDER BY id ASC""",(since_id,current_user_id,user_id,user_id,current_user_id)).fetchall()
    state_rows=conn.execute("SELECT id,is_read,delivered_at,read_at FROM messages WHERE sender_id=? AND receiver_id=? ORDER BY id DESC LIMIT 100",(current_user_id,user_id)).fetchall()
    conn.commit(); conn.close()
    return jsonify({"messages":[dict(r) for r in rows],"sent_state":[dict(r) for r in state_rows],"blocked":blocked})


@app.route("/api/message-notifications")
def api_message_notifications():
    if not user_required():
        return jsonify({"error":"login_required"}), 401
    uid=session["user_id"]
    conn=get_db_connection()
    rows=conn.execute("""
        SELECT m.id,m.sender_id,m.receiver_id,m.message,m.created_at,u.name,u.username,u.photo
        FROM messages m JOIN users u ON u.id=m.sender_id
        WHERE m.receiver_id=? AND m.is_read=0
          AND (m.expires_at IS NULL OR m.expires_at>?)
        ORDER BY m.id DESC LIMIT 10
    """, (uid, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))).fetchall()
    unread=conn.execute("SELECT COUNT(*) FROM messages WHERE receiver_id=? AND is_read=0", (uid,)).fetchone()[0]
    conn.close()
    return jsonify({"messages":[dict(r) for r in rows],"unread_count":unread})


@app.route("/api/users/<int:user_id>/block", methods=["POST"])
def block_user(user_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    require_csrf(); uid=session["user_id"]
    if uid==user_id: return jsonify({"error":"invalid_user"}),400
    conn=get_db_connection()
    if not conn.execute("SELECT id FROM users WHERE id=?",(user_id,)).fetchone(): conn.close(); return jsonify({"error":"user_not_found"}),404
    now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT OR IGNORE INTO user_blocks(blocker_id,blocked_id,created_at) VALUES(?,?,?)",(uid,user_id,now))
    conn.commit(); conn.close(); return jsonify({"ok":True,"blocked":True})


@app.route("/api/users/<int:user_id>/unblock", methods=["POST"])
def unblock_user(user_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    require_csrf(); uid=session["user_id"]; conn=get_db_connection()
    conn.execute("DELETE FROM user_blocks WHERE blocker_id=? AND blocked_id=?",(uid,user_id)); conn.commit(); conn.close()
    return jsonify({"ok":True,"blocked":False})


@app.route("/api/users/<int:user_id>/report", methods=["POST"])
def report_user(user_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    require_csrf(); uid=session["user_id"]; data=request.get_json(silent=True) or {}
    reason=str(data.get("reason", "spam")).strip()[:80]; details=str(data.get("details", "")).strip()[:1000]
    if reason not in {"spam","harassment","scam","inappropriate","other"}: reason="other"
    conn=get_db_connection()
    if not conn.execute("SELECT id FROM users WHERE id=?",(user_id,)).fetchone(): conn.close(); return jsonify({"error":"user_not_found"}),404
    conn.execute("INSERT INTO message_reports(reporter_id,reported_user_id,reason,details,created_at) VALUES(?,?,?,?,?)",(uid,user_id,reason,details,datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit(); conn.close(); return jsonify({"ok":True})


@app.route("/admin/message-reports")
def admin_message_reports():
    if not admin_required():
        return redirect(url_for("login"))
    conn=get_db_connection()
    reports=conn.execute("""
        SELECT r.*, reporter.name AS reporter_name, reported.name AS reported_name, reported.username AS reported_username
        FROM message_reports r
        JOIN users reporter ON reporter.id=r.reporter_id
        JOIN users reported ON reported.id=r.reported_user_id
        ORDER BY r.id DESC
    """).fetchall()
    conn.close()
    return render_template("message_reports.html", reports=reports)


# ---------------------------------------------------------
# Campus services: announcements, clubs, events and notifications
# ---------------------------------------------------------

@app.route("/campus")
def campus_services():
    if not user_required():
        return redirect(url_for("user_login"))
    conn = get_db_connection()
    announcements = conn.execute("SELECT * FROM announcements ORDER BY id DESC LIMIT 20").fetchall()
    clubs = conn.execute("SELECT * FROM clubs ORDER BY name COLLATE NOCASE").fetchall()
    events = conn.execute("SELECT * FROM events ORDER BY event_date ASC, id DESC").fetchall()
    user_id = session["user_id"]
    joined = {r["club_id"] for r in conn.execute("SELECT club_id FROM club_members WHERE user_id=?", (user_id,)).fetchall()}
    registered = {r["event_id"] for r in conn.execute("SELECT event_id FROM event_registrations WHERE user_id=?", (user_id,)).fetchall()}
    notifications = conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 20", (user_id,)).fetchall()
    conn.close()
    return render_template("campus.html", announcements=announcements, clubs=clubs, events=events, joined=joined, registered=registered, notifications=notifications, csrf=csrf_token())


@app.route("/clubs/<int:club_id>/join", methods=["POST"])
def join_club(club_id):
    if not user_required(): return jsonify({"error":"login_required"}), 401
    require_csrf()
    conn=get_db_connection(); uid=session["user_id"]
    if not conn.execute("SELECT id FROM clubs WHERE id=?", (club_id,)).fetchone():
        conn.close(); return jsonify({"error":"club_not_found"}), 404
    exists=conn.execute("SELECT 1 FROM club_members WHERE club_id=? AND user_id=?", (club_id,uid)).fetchone()
    if exists:
        conn.execute("DELETE FROM club_members WHERE club_id=? AND user_id=?", (club_id,uid)); joined=False
    else:
        conn.execute("INSERT INTO club_members(club_id,user_id,joined_at) VALUES(?,?,?)", (club_id,uid,datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))); joined=True
    conn.commit(); conn.close()
    return jsonify({"joined":joined})


@app.route("/events/<int:event_id>/register", methods=["POST"])
def register_event(event_id):
    if not user_required(): return jsonify({"error":"login_required"}), 401
    require_csrf()
    conn=get_db_connection(); uid=session["user_id"]
    if not conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone():
        conn.close(); return jsonify({"error":"event_not_found"}), 404
    exists=conn.execute("SELECT 1 FROM event_registrations WHERE event_id=? AND user_id=?", (event_id,uid)).fetchone()
    if exists:
        conn.execute("DELETE FROM event_registrations WHERE event_id=? AND user_id=?", (event_id,uid)); registered=False
    else:
        conn.execute("INSERT INTO event_registrations(event_id,user_id,registered_at) VALUES(?,?,?)", (event_id,uid,datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))); registered=True
    conn.commit(); conn.close()
    return jsonify({"registered":registered})


@app.route("/notifications/<int:notification_id>/read", methods=["POST"])
def mark_notification_read(notification_id):
    if not user_required(): return jsonify({"error":"login_required"}), 401
    require_csrf()
    conn=get_db_connection()
    conn.execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?", (notification_id, session["user_id"]))
    conn.commit(); conn.close()
    return jsonify({"ok":True})


@app.route("/api/notifications")
def api_notifications():
    if not user_required(): return jsonify({"error":"login_required"}), 401
    conn=get_db_connection(); uid=session["user_id"]
    rows=conn.execute("SELECT id,title,body,is_read,created_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 30", (uid,)).fetchall()
    unread=conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (uid,)).fetchone()[0]
    conn.close()
    return jsonify({"notifications":[dict(r) for r in rows],"unread_count":unread})


@app.route("/admin/campus", methods=["GET", "POST"])
def admin_campus():
    if not admin_required(): return redirect(url_for("login"))
    conn=get_db_connection()
    if request.method == "POST":
        require_csrf()
        kind=request.form.get("kind", "").strip()
        now=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        try:
            if kind == "university":
                name=request.form.get("name", "").strip()
                domain=request.form.get("domain", "").strip().lower()
                description=request.form.get("description", "").strip()
                if not name: raise ValueError("University name is required.")
                cur=conn.execute("INSERT INTO universities(name,domain,description,created_at) VALUES(?,?,?,?)",(name,domain,description,now))
                uid=cur.lastrowid
                conn.execute("INSERT OR IGNORE INTO branding_settings(university_id) VALUES(?)",(uid,))
                flash("University created. You can now create its departments, batches and groups.","success")
            elif kind == "department":
                uid=request.form.get("university_id",type=int); name=request.form.get("name","").strip(); code=request.form.get("code","").strip(); desc=request.form.get("description","").strip()
                if not uid or not name: raise ValueError("University and department name are required.")
                conn.execute("INSERT INTO departments(university_id,name,code,description,created_at) VALUES(?,?,?,?,?)",(uid,name,code,desc,now)); flash("Department created.","success")
            elif kind == "batch":
                uid=request.form.get("university_id",type=int); did=request.form.get("department_id",type=int) or None; name=request.form.get("name","").strip(); sy=request.form.get("start_year",type=int); ey=request.form.get("end_year",type=int)
                if not uid or not name: raise ValueError("University and batch name are required.")
                conn.execute("INSERT INTO batches(university_id,department_id,name,start_year,end_year,created_at) VALUES(?,?,?,?,?,?)",(uid,did,name,sy,ey,now)); flash("Batch created.","success")
            elif kind == "group":
                uid=request.form.get("university_id",type=int); did=request.form.get("department_id",type=int) or None; name=request.form.get("name","").strip(); desc=request.form.get("description","").strip(); privacy=request.form.get("privacy","university"); admin_user_id=request.form.get("group_admin_user_id",type=int) or None
                if not uid or not name: raise ValueError("University and group name are required.")
                if admin_user_id and not conn.execute("SELECT id FROM users WHERE id=? AND university_id=?",(admin_user_id,uid)).fetchone(): raise ValueError("Selected group administrator must belong to the selected university.")
                if not admin_user_id:
                    candidate=conn.execute("SELECT id FROM users WHERE university_id=? ORDER BY id LIMIT 1",(uid,)).fetchone()
                    admin_user_id=candidate["id"] if candidate else None
                cur=conn.execute("INSERT INTO groups(university_id,department_id,name,description,privacy,created_by,created_at) VALUES(?,?,?,?,?,?,?)",(uid,did,name,desc,privacy,admin_user_id,now)); gid=cur.lastrowid
                if admin_user_id:
                    conn.execute("INSERT OR IGNORE INTO group_members(group_id,user_id,joined_at) VALUES(?,?,?)",(gid,admin_user_id,now))
                    conn.execute("INSERT OR IGNORE INTO group_admins(group_id,user_id,created_at) VALUES(?,?,?)",(gid,admin_user_id,now))
                flash("Group created with a member administrator.","success")
            elif kind == "announcement":
                uid=request.form.get("university_id",type=int) or None; title=request.form.get("title","").strip(); body=request.form.get("body","").strip()
                if not title or not body: raise ValueError("Title and announcement body are required.")
                conn.execute("INSERT INTO announcements(university_id,title,body,created_at) VALUES(?,?,?,?)",(uid,title,body,now)); flash("Announcement published.","success")
            elif kind == "club":
                uid=request.form.get("university_id",type=int) or None; title=request.form.get("title","").strip(); body=request.form.get("body","").strip()
                if not title: raise ValueError("Club name is required.")
                conn.execute("INSERT INTO clubs(university_id,name,description,created_at) VALUES(?,?,?,?)",(uid,title,body,now)); flash("Club created.","success")
            elif kind == "event":
                uid=request.form.get("university_id",type=int) or None; title=request.form.get("title","").strip(); body=request.form.get("body","").strip(); date=request.form.get("event_date","").strip(); loc=request.form.get("location","").strip()
                if not title or not date: raise ValueError("Event title and date are required.")
                conn.execute("INSERT INTO events(university_id,title,description,event_date,location,created_at) VALUES(?,?,?,?,?,?)",(uid,title,body,date,loc,now)); flash("Event created.","success")
            elif kind == "branding":
                uid=request.form.get("university_id",type=int); primary=request.form.get("primary_color","#1456c4").strip(); secondary=request.form.get("secondary_color","#0f172a").strip(); accent=request.form.get("accent_color","#22c55e").strip(); domain=request.form.get("custom_domain","").strip().lower()
                if not uid: raise ValueError("Choose a university.")
                conn.execute("INSERT INTO branding_settings(university_id,primary_color,secondary_color,accent_color,custom_domain) VALUES(?,?,?,?,?) ON CONFLICT(university_id) DO UPDATE SET primary_color=excluded.primary_color,secondary_color=excluded.secondary_color,accent_color=excluded.accent_color,custom_domain=excluded.custom_domain",(uid,primary,secondary,accent,domain)); flash("Branding settings saved.","success")
            elif kind == "assign_admin":
                uid=request.form.get("user_id",type=int); rid=conn.execute("SELECT id FROM roles WHERE name='UNIVERSITY_ADMIN'").fetchone();
                if not uid or not rid: raise ValueError("Choose a user.")
                conn.execute("INSERT OR IGNORE INTO user_roles(user_id,role_id) VALUES(?,?)",(uid,rid[0])); flash("University administrator role assigned.","success")
            else: raise ValueError("Unknown management action.")
            conn.commit()
        except Exception as e:
            conn.rollback(); flash(str(e),"error")
        conn.close(); return redirect(url_for("admin_campus"))
    universities=conn.execute("SELECT * FROM universities ORDER BY name COLLATE NOCASE").fetchall()
    departments=conn.execute("SELECT d.*,u.name university_name,(SELECT COUNT(*) FROM users x WHERE x.department_id=d.id) member_count FROM departments d JOIN universities u ON u.id=d.university_id ORDER BY u.name,d.name").fetchall()
    batches=conn.execute("SELECT b.*,u.name university_name,d.name department_name,(SELECT COUNT(*) FROM users x WHERE x.batch_id=b.id) member_count FROM batches b JOIN universities u ON u.id=b.university_id LEFT JOIN departments d ON d.id=b.department_id ORDER BY u.name,b.name").fetchall()
    groups=conn.execute("SELECT g.*,u.name university_name,d.name department_name,(SELECT COUNT(*) FROM group_members gm WHERE gm.group_id=g.id) member_count FROM groups g JOIN universities u ON u.id=g.university_id LEFT JOIN departments d ON d.id=g.department_id ORDER BY g.id DESC").fetchall()
    users=conn.execute("SELECT id,name,username,gmail FROM users ORDER BY name COLLATE NOCASE").fetchall()
    announcements=conn.execute("SELECT a.*,u.name university_name FROM announcements a LEFT JOIN universities u ON u.id=a.university_id ORDER BY a.id DESC").fetchall()
    clubs=conn.execute("SELECT c.*,u.name university_name FROM clubs c LEFT JOIN universities u ON u.id=c.university_id ORDER BY c.id DESC").fetchall()
    events=conn.execute("SELECT e.*,u.name university_name,(SELECT COUNT(*) FROM event_registrations er WHERE er.event_id=e.id) registration_count FROM events e LEFT JOIN universities u ON u.id=e.university_id ORDER BY e.event_date ASC").fetchall()
    conn.close()
    return render_template("admin_campus.html",universities=universities,departments=departments,batches=batches,groups=groups,users=users,announcements=announcements,clubs=clubs,events=events,csrf=csrf_token())


def group_access(conn, group_id, uid):
    group=conn.execute("SELECT * FROM groups WHERE id=?",(group_id,)).fetchone()
    user=conn.execute("SELECT university_id,department_id FROM users WHERE id=?",(uid,)).fetchone()
    if not group or not user: return group, user, False
    member=conn.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",(group_id,uid)).fetchone()
    admin=conn.execute("SELECT 1 FROM group_admins WHERE group_id=? AND user_id=?",(group_id,uid)).fetchone()
    owner=(group["created_by"] == uid)
    privacy=group["privacy"]
    eligible = privacy == "public" or (privacy == "university" and user["university_id"] == group["university_id"]) or (privacy == "department" and user["department_id"] == group["department_id"] and user["university_id"] == group["university_id"]) or member or owner or admin
    return group,user,bool(eligible)

@app.route("/groups")
def groups_page():
    if not user_required(): return redirect(url_for("user_login"))
    conn=get_db_connection(); uid=session["user_id"]
    user=conn.execute("SELECT university_id,department_id FROM users WHERE id=?",(uid,)).fetchone()
    groups=conn.execute("""
        SELECT g.*,d.name department_name,u.name university_name,
        (SELECT COUNT(*) FROM group_members gm WHERE gm.group_id=g.id) member_count,
        EXISTS(SELECT 1 FROM group_members gm WHERE gm.group_id=g.id AND gm.user_id=?) joined,
        EXISTS(SELECT 1 FROM group_admins ga WHERE ga.group_id=g.id AND ga.user_id=?) is_admin
        FROM groups g LEFT JOIN departments d ON d.id=g.department_id LEFT JOIN universities u ON u.id=g.university_id
        WHERE g.privacy='public' OR (g.privacy='university' AND g.university_id=?) OR (g.privacy='department' AND g.university_id=? AND g.department_id=?)
           OR g.created_by=? OR EXISTS(SELECT 1 FROM group_members gm2 WHERE gm2.group_id=g.id AND gm2.user_id=?)
        ORDER BY g.name
    """,(uid,uid,user["university_id"] if user else None,user["university_id"] if user else None,user["department_id"] if user else None,uid,uid)).fetchall()
    departments=conn.execute("SELECT id,name,code FROM departments WHERE university_id=? ORDER BY name",(user["university_id"],)).fetchall() if user and user["university_id"] else []
    conn.close(); return render_template("groups.html",groups=groups,csrf=csrf_token(),user=user,departments=departments)

@app.route("/groups/create",methods=["POST"])
def create_group():
    if not user_required(): return redirect(url_for("user_login"))
    require_csrf(); conn=get_db_connection(); uid=session["user_id"]
    user=conn.execute("SELECT university_id,department_id FROM users WHERE id=?",(uid,)).fetchone()
    name=request.form.get("name","").strip(); desc=request.form.get("description","").strip(); privacy=request.form.get("privacy","university").strip(); did=request.form.get("department_id",type=int) or None
    if not name or privacy not in {"public","university","department","private"}: conn.close(); flash("Group name and a valid privacy option are required.","error"); return redirect(url_for("groups_page"))
    if not user or not user["university_id"]: conn.close(); flash("Your account must be linked to a university before creating a group.","error"); return redirect(url_for("groups_page"))
    if privacy=="department" and not did: conn.close(); flash("Choose a department for a department-only group.","error"); return redirect(url_for("groups_page"))
    if did and not conn.execute("SELECT 1 FROM departments WHERE id=? AND university_id=?",(did,user["university_id"])).fetchone(): conn.close(); flash("Invalid department.","error"); return redirect(url_for("groups_page"))
    now=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur=conn.execute("INSERT INTO groups(university_id,department_id,name,description,privacy,created_by,created_at) VALUES(?,?,?,?,?,?,?)",(user["university_id"],did,name,desc,privacy,uid,now))
    gid=cur.lastrowid; conn.execute("INSERT INTO group_members(group_id,user_id,joined_at) VALUES(?,?,?)",(gid,uid,now)); conn.execute("INSERT INTO group_admins(group_id,user_id,created_at) VALUES(?,?,?)",(gid,uid,now)); conn.commit(); conn.close()
    flash("Group created. You are the owner and first group administrator.","success"); return redirect(url_for("group_detail",group_id=gid))

@app.route("/groups/<int:group_id>")
def group_detail(group_id):
    if not user_required(): return redirect(url_for("user_login"))
    conn=get_db_connection(); uid=session["user_id"]; group,user,eligible=group_access(conn,group_id,uid)
    if not group: conn.close(); abort(404)
    member=bool(conn.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",(group_id,uid)).fetchone())
    is_admin=bool(conn.execute("SELECT 1 FROM group_admins WHERE group_id=? AND user_id=?",(group_id,uid)).fetchone()) or group["created_by"]==uid
    pending=bool(conn.execute("SELECT 1 FROM group_join_requests WHERE group_id=? AND user_id=? AND status='pending'",(group_id,uid)).fetchone())
    if group["privacy"]=="private" and not member and not is_admin: eligible=False
    posts=conn.execute("""SELECT p.*,u.name,u.photo, (SELECT COUNT(*) FROM group_post_likes l WHERE l.post_id=p.id) like_count,
        EXISTS(SELECT 1 FROM group_post_likes l2 WHERE l2.post_id=p.id AND l2.user_id=?) liked_by_me,
        (SELECT COUNT(*) FROM group_post_comments c WHERE c.post_id=p.id) comment_count
        FROM group_posts p JOIN users u ON u.id=p.user_id WHERE p.group_id=? ORDER BY p.id DESC""",(uid,group_id)).fetchall() if eligible else []
    comments={p["id"]:conn.execute("SELECT c.*,u.name FROM group_post_comments c JOIN users u ON u.id=c.user_id WHERE c.post_id=? ORDER BY c.id",(p["id"],)).fetchall() for p in posts}
    members=conn.execute("SELECT u.id,u.name,u.username,u.photo,EXISTS(SELECT 1 FROM group_admins ga WHERE ga.group_id=? AND ga.user_id=u.id) is_admin FROM users u JOIN group_members gm ON gm.user_id=u.id WHERE gm.group_id=? ORDER BY u.name",(group_id,group_id)).fetchall()
    requests=conn.execute("SELECT u.id,u.name,u.username FROM group_join_requests r JOIN users u ON u.id=r.user_id WHERE r.group_id=? AND r.status='pending' ORDER BY r.created_at",(group_id,)).fetchall() if is_admin else []
    conn.close(); return render_template("group_detail.html",group=group,posts=posts,comments=comments,members=members,requests=requests,member=member,is_admin=is_admin,pending=pending,eligible=eligible,csrf=csrf_token())

@app.route("/groups/<int:group_id>/join",methods=["POST"])
def join_group(group_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    require_csrf(); conn=get_db_connection(); uid=session["user_id"]; group=conn.execute("SELECT * FROM groups WHERE id=?",(group_id,)).fetchone(); user=conn.execute("SELECT university_id,department_id FROM users WHERE id=?",(uid,)).fetchone()
    if not group: conn.close(); return jsonify({"error":"group_not_found"}),404
    exists=conn.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",(group_id,uid)).fetchone()
    if exists:
        conn.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?",(group_id,uid)); conn.execute("DELETE FROM group_join_requests WHERE group_id=? AND user_id=?",(group_id,uid)); joined=False; status="left"
        notify_group_admins(conn, group_id, "Member left group", f"A member has left {group['name']}.", exclude_user_id=uid)
    else:
        eligible=group["privacy"]=="public" or (group["privacy"]=="university" and user["university_id"]==group["university_id"]) or (group["privacy"]=="department" and user["university_id"]==group["university_id"] and user["department_id"]==group["department_id"])
        if group["privacy"]=="private":
            conn.execute("INSERT OR REPLACE INTO group_join_requests(group_id,user_id,status,created_at) VALUES(?,?,?,?)",(group_id,uid,"pending",datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
            notify_group_admins(conn, group_id, "New join request", f"A user requested to join {group['name']}.", exclude_user_id=uid)
            conn.commit(); conn.close(); return jsonify({"joined":False,"pending":True,"status":"pending"})
        if not eligible: conn.close(); return jsonify({"error":"not_eligible"}),403
        conn.execute("INSERT INTO group_members(group_id,user_id,joined_at) VALUES(?,?,?)",(group_id,uid,datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))); joined=True; status="joined"
        notify_group_admins(conn, group_id, "New group member", f"A new member joined {group['name']}.", exclude_user_id=uid)
        notify_user(conn, uid, "Joined group", f"You joined {group['name']}.")
    conn.commit(); conn.close(); return jsonify({"joined":joined,"pending":False,"status":status})

@app.route("/groups/<int:group_id>/post",methods=["POST"])
def group_post(group_id):
    if not user_required(): return redirect(url_for("user_login"))
    require_csrf(); conn=get_db_connection(); uid=session["user_id"]; group,user,eligible=group_access(conn,group_id,uid); member=conn.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",(group_id,uid)).fetchone()
    body=request.form.get("body","").strip()
    if not group or not member or not body: conn.close(); flash("You must be a group member and provide a post.","error"); return redirect(url_for("group_detail",group_id=group_id))
    conn.execute("INSERT INTO group_posts(group_id,user_id,body,created_at) VALUES(?,?,?,?)",(group_id,uid,body,datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
    notify_group_admins(conn, group_id, "New group post", f"A new post was published in {group['name']}.", exclude_user_id=uid)
    conn.commit(); conn.close(); return redirect(url_for("group_detail",group_id=group_id))

@app.route("/group-post/<int:post_id>/like",methods=["POST"])
def group_post_like(post_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    require_csrf(); conn=get_db_connection(); uid=session["user_id"]; post=conn.execute("SELECT * FROM group_posts WHERE id=?",(post_id,)).fetchone(); member=conn.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",(post["group_id"],uid)).fetchone() if post else None
    if not post or not member: conn.close(); return jsonify({"error":"not_allowed"}),403
    exists=conn.execute("SELECT 1 FROM group_post_likes WHERE post_id=? AND user_id=?",(post_id,uid)).fetchone()
    if exists: conn.execute("DELETE FROM group_post_likes WHERE post_id=? AND user_id=?",(post_id,uid)); liked=False
    else: conn.execute("INSERT INTO group_post_likes(post_id,user_id,created_at) VALUES(?,?,?)",(post_id,uid,datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))); liked=True
    count=conn.execute("SELECT COUNT(*) FROM group_post_likes WHERE post_id=?",(post_id,)).fetchone()[0]; conn.commit(); conn.close(); return jsonify({"liked":liked,"count":count})

@app.route("/group-post/<int:post_id>/comment",methods=["POST"])
def group_post_comment(post_id):
    if not user_required(): return redirect(url_for("user_login"))
    require_csrf(); conn=get_db_connection(); uid=session["user_id"]; post=conn.execute("SELECT * FROM group_posts WHERE id=?",(post_id,)).fetchone(); member=conn.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",(post["group_id"],uid)).fetchone() if post else None; comment=request.form.get("comment","").strip()
    if not post or not member or not comment: conn.close(); flash("Comment cannot be empty.","error"); return redirect(url_for("group_detail",group_id=post["group_id"] if post else 1))
    conn.execute("INSERT INTO group_post_comments(post_id,user_id,comment,created_at) VALUES(?,?,?,?)",(post_id,uid,comment,datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
    if post["user_id"] != uid: notify_user(conn, post["user_id"], "New comment", "Someone commented on your group post.")
    notify_group_admins(conn, post["group_id"], "New group comment", "A new comment was added to a group post.", exclude_user_id=uid)
    conn.commit(); gid=post["group_id"]; conn.close(); return redirect(url_for("group_detail",group_id=gid))

@app.route("/groups/<int:group_id>/members/<int:user_id>/remove",methods=["POST"])
def group_remove_member(group_id,user_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    require_csrf(); conn=get_db_connection(); uid=session["user_id"]; group=conn.execute("SELECT * FROM groups WHERE id=?",(group_id,)).fetchone(); admin=bool(conn.execute("SELECT 1 FROM group_admins WHERE group_id=? AND user_id=?",(group_id,uid)).fetchone()) or (group and group["created_by"]==uid)
    if not admin or user_id==group["created_by"]: conn.close(); return jsonify({"error":"not_allowed"}),403
    conn.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?",(group_id,user_id)); conn.execute("DELETE FROM group_admins WHERE group_id=? AND user_id=?",(group_id,user_id)); conn.commit(); conn.close(); return jsonify({"ok":True})

@app.route("/groups/<int:group_id>/admins/<int:user_id>",methods=["POST"])
def group_toggle_admin(group_id,user_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    require_csrf(); conn=get_db_connection(); uid=session["user_id"]; group=conn.execute("SELECT * FROM groups WHERE id=?",(group_id,)).fetchone(); owner=group and group["created_by"]==uid; member=bool(conn.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",(group_id,user_id)).fetchone())
    if not owner or not member or user_id==uid: conn.close(); return jsonify({"error":"owner_only"}),403
    exists=conn.execute("SELECT 1 FROM group_admins WHERE group_id=? AND user_id=?",(group_id,user_id)).fetchone()
    if exists: conn.execute("DELETE FROM group_admins WHERE group_id=? AND user_id=?",(group_id,user_id)); state=False
    else: conn.execute("INSERT INTO group_admins(group_id,user_id,created_at) VALUES(?,?,?)",(group_id,user_id,datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))); state=True
    conn.commit(); conn.close(); return jsonify({"is_admin":state})

@app.route("/groups/<int:group_id>/requests/<int:user_id>",methods=["POST"])
def group_request_action(group_id,user_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    require_csrf(); action=request.form.get("action","approve"); conn=get_db_connection(); uid=session["user_id"]; group=conn.execute("SELECT * FROM groups WHERE id=?",(group_id,)).fetchone(); admin=bool(conn.execute("SELECT 1 FROM group_admins WHERE group_id=? AND user_id=?",(group_id,uid)).fetchone()) or (group and group["created_by"]==uid)
    if not admin: conn.close(); return jsonify({"error":"not_allowed"}),403
    if action=="approve":
        conn.execute("INSERT OR IGNORE INTO group_members(group_id,user_id,joined_at) VALUES(?,?,?)",(group_id,user_id,datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
        notify_user(conn,user_id,"Join request approved",f"Your request to join {group['name']} was approved.")
    else:
        notify_user(conn,user_id,"Join request rejected",f"Your request to join {group['name']} was rejected.")
    conn.execute("UPDATE group_join_requests SET status=? WHERE group_id=? AND user_id=?",("approved" if action=="approve" else "rejected",group_id,user_id)); conn.commit(); conn.close(); return redirect(url_for("group_detail",group_id=group_id))

@app.route("/api/groups/<int:group_id>/updates")
def group_updates(group_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    conn=get_db_connection(); uid=session["user_id"]; group,user,eligible=group_access(conn,group_id,uid)
    if not group or not eligible:
        conn.close(); return jsonify({"error":"not_allowed"}),403
    member=bool(conn.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",(group_id,uid)).fetchone())
    if not member:
        conn.close(); return jsonify({"error":"not_member"}),403
    last_id=request.args.get("since",type=int) or 0
    post_count=conn.execute("SELECT COUNT(*) FROM group_posts WHERE group_id=?",(group_id,)).fetchone()[0]
    member_count=conn.execute("SELECT COUNT(*) FROM group_members WHERE group_id=?",(group_id,)).fetchone()[0]
    request_count=conn.execute("SELECT COUNT(*) FROM group_join_requests WHERE group_id=? AND status='pending'",(group_id,)).fetchone()[0]
    latest=conn.execute("SELECT id,created_at FROM group_posts WHERE group_id=? ORDER BY id DESC LIMIT 1",(group_id,)).fetchone()
    new_posts=conn.execute("SELECT p.id,p.body,p.created_at,u.name FROM group_posts p JOIN users u ON u.id=p.user_id WHERE p.group_id=? AND p.id>? ORDER BY p.id",(group_id,last_id)).fetchall()
    conn.close(); return jsonify({"post_count":post_count,"member_count":member_count,"pending_requests":request_count,"latest_post_id":latest["id"] if latest else 0,"new_posts":[dict(r) for r in new_posts]})


# ---------------------------------------------------------
# Chat groups
# ---------------------------------------------------------

def chat_group_member(conn, group_id, user_id):
    return conn.execute("SELECT role FROM chat_group_members WHERE chat_group_id=? AND user_id=?", (group_id, user_id)).fetchone()


def chat_group_visible(conn, group_id, user_id):
    group=conn.execute("SELECT * FROM chat_groups WHERE id=?", (group_id,)).fetchone()
    if not group: return None, False
    member=bool(chat_group_member(conn, group_id, user_id))
    if not member and group["privacy"]=="linked" and group["source_group_id"]:
        if conn.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",(group["source_group_id"],user_id)).fetchone():
            conn.execute("INSERT OR IGNORE INTO chat_group_members(chat_group_id,user_id,role,joined_at) VALUES(?,?,?,?)",(group_id,user_id,"member",datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit(); member=True
    return group, member


@app.route("/chat-groups/create", methods=["POST"])
def create_chat_group():
    if not user_required(): return redirect(url_for("user_login"))
    require_csrf(); uid=session["user_id"]
    name=request.form.get("name","").strip()[:120]
    description=request.form.get("description","").strip()[:500]
    kind=request.form.get("kind","open")
    source_group_id=request.form.get("source_group_id",type=int)
    if not name:
        flash("Chat group name is required.","error"); return redirect(url_for("messages"))
    conn=get_db_connection(); now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    members=[uid]
    privacy="open"
    if kind=="linked":
        source=conn.execute("SELECT * FROM groups WHERE id=?",(source_group_id,)).fetchone() if source_group_id else None
        if not source or not conn.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",(source_group_id,uid)).fetchone():
            conn.close(); flash("You must be a member of the selected UniversityConnect group to create its chat.","error"); return redirect(url_for("messages"))
        members=[r["user_id"] for r in conn.execute("SELECT user_id FROM group_members WHERE group_id=?",(source_group_id,)).fetchall()]
        if uid not in members: members.append(uid)
        privacy="linked"
    cursor=conn.execute("INSERT INTO chat_groups(name,description,privacy,created_by,source_group_id,created_at) VALUES(?,?,?,?,?,?)",(name,description,privacy,uid,source_group_id if kind=="linked" else None,now))
    gid=cursor.lastrowid
    for member_id in members:
        role="owner" if member_id==uid else "member"
        conn.execute("INSERT OR IGNORE INTO chat_group_members(chat_group_id,user_id,role,joined_at) VALUES(?,?,?,?)",(gid,member_id,role,now))
    conn.commit(); conn.close()
    flash("Chat group created.","success")
    return redirect(url_for("chat_group",group_id=gid))


@app.route("/chat-groups/<int:group_id>")
def chat_group(group_id):
    if not user_required(): return redirect(url_for("user_login"))
    uid=session["user_id"]; conn=get_db_connection(); group,member=chat_group_visible(conn,group_id,uid)
    if not group: conn.close(); abort(404)
    if not member:
        conn.close(); return render_template("chat_group.html",group=group,member=False,messages=[],members=[],csrf=csrf_token(),can_join=group["privacy"]=="open")
    rows=conn.execute("SELECT m.*,u.name,u.photo FROM chat_group_messages m JOIN users u ON u.id=m.sender_id WHERE m.chat_group_id=? ORDER BY m.id DESC LIMIT 200",(group_id,)).fetchall()
    rows=list(reversed(rows))
    members=conn.execute("SELECT u.id,u.name,u.username,u.photo,m.role FROM chat_group_members m JOIN users u ON u.id=m.user_id WHERE m.chat_group_id=? ORDER BY CASE WHEN m.role='owner' THEN 0 ELSE 1 END,u.name",(group_id,)).fetchall()
    conn.close(); return render_template("chat_group.html",group=group,member=True,messages=rows,members=members,csrf=csrf_token(),can_join=False,current_user_id=uid)


@app.route("/chat-groups/<int:group_id>/join",methods=["POST"])
def join_chat_group(group_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    require_csrf(); uid=session["user_id"]; conn=get_db_connection(); group=conn.execute("SELECT * FROM chat_groups WHERE id=?",(group_id,)).fetchone()
    if not group: conn.close(); return jsonify({"error":"not_found"}),404
    if group["privacy"]=="linked":
        if not group["source_group_id"] or not conn.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",(group["source_group_id"],uid)).fetchone():
            conn.close(); return jsonify({"error":"You must be a member of the linked UniversityConnect group."}),403
    elif group["privacy"]!="open":
        conn.close(); return jsonify({"error":"This chat is membership-controlled."}),403
    conn.execute("INSERT OR IGNORE INTO chat_group_members(chat_group_id,user_id,role,joined_at) VALUES(?,?,?,?)",(group_id,uid,"member",datetime.now().strftime("%Y-%m-%d %H:%M:%S"))); conn.commit(); conn.close(); return jsonify({"ok":True})


@app.route("/chat-groups/<int:group_id>/leave",methods=["POST"])
def leave_chat_group(group_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    require_csrf(); uid=session["user_id"]; conn=get_db_connection(); group=conn.execute("SELECT * FROM chat_groups WHERE id=?",(group_id,)).fetchone()
    member=conn.execute("SELECT role FROM chat_group_members WHERE chat_group_id=? AND user_id=?",(group_id,uid)).fetchone()
    if not group or not member: conn.close(); return jsonify({"error":"not_member"}),403
    if group["created_by"]==uid:
        conn.close(); return jsonify({"error":"The owner cannot leave. Transfer ownership or delete the chat group first."}),400
    conn.execute("DELETE FROM chat_group_members WHERE chat_group_id=? AND user_id=?",(group_id,uid)); conn.commit(); conn.close(); return jsonify({"ok":True})


@app.route("/chat-groups/<int:group_id>/send",methods=["POST"])
def send_chat_group_message(group_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    require_csrf(); uid=session["user_id"]; text=request.form.get("message","").strip()[:5000]
    if not text: return jsonify({"error":"Message cannot be empty."}),400
    conn=get_db_connection(); group=conn.execute("SELECT id FROM chat_groups WHERE id=?",(group_id,)).fetchone(); member=chat_group_member(conn,group_id,uid)
    if not group or not member: conn.close(); return jsonify({"error":"not_allowed"}),403
    now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"); cur=conn.execute("INSERT INTO chat_group_messages(chat_group_id,sender_id,message,created_at) VALUES(?,?,?,?)",(group_id,uid,text,now)); mid=cur.lastrowid; conn.commit()
    row=conn.execute("SELECT m.*,u.name,u.photo FROM chat_group_messages m JOIN users u ON u.id=m.sender_id WHERE m.id=?",(mid,)).fetchone(); conn.close()
    return jsonify({"ok":True,"message":dict(row)})


@app.route("/api/chat-groups/<int:group_id>/messages")
def api_chat_group_messages(group_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    uid=session["user_id"]
    try: since=max(0,int(request.args.get("since",0)))
    except (TypeError,ValueError): since=0
    conn=get_db_connection(); group=conn.execute("SELECT id FROM chat_groups WHERE id=?",(group_id,)).fetchone(); member=chat_group_member(conn,group_id,uid)
    if not group or not member: conn.close(); return jsonify({"error":"not_allowed"}),403
    rows=conn.execute("SELECT m.*,u.name,u.photo FROM chat_group_messages m JOIN users u ON u.id=m.sender_id WHERE m.chat_group_id=? AND m.id>? ORDER BY m.id ASC LIMIT 100",(group_id,since)).fetchall(); conn.close()
    return jsonify({"messages":[dict(r) for r in rows]})


@app.route("/chat-groups")
def chat_groups_page():
    if not user_required(): return redirect(url_for("user_login"))
    uid=session["user_id"]; conn=get_db_connection()
    groups=conn.execute("""SELECT cg.*,u.name AS creator_name,
        (SELECT COUNT(*) FROM chat_group_members cm WHERE cm.chat_group_id=cg.id) AS member_count,
        (EXISTS(SELECT 1 FROM chat_group_members me WHERE me.chat_group_id=cg.id AND me.user_id=?)
         OR (cg.privacy='linked' AND EXISTS(SELECT 1 FROM group_members gm WHERE gm.group_id=cg.source_group_id AND gm.user_id=?))) AS joined
        FROM chat_groups cg JOIN users u ON u.id=cg.created_by
        WHERE cg.privacy='open'
           OR EXISTS(SELECT 1 FROM chat_group_members mine WHERE mine.chat_group_id=cg.id AND mine.user_id=?)
           OR (cg.privacy='linked' AND EXISTS(SELECT 1 FROM group_members source_member WHERE source_member.group_id=cg.source_group_id AND source_member.user_id=?))
        ORDER BY cg.id DESC""",(uid,uid,uid,uid)).fetchall()
    social_groups=conn.execute("""SELECT g.id,g.name FROM groups g JOIN group_members gm ON gm.group_id=g.id WHERE gm.user_id=? ORDER BY g.name""",(uid,)).fetchall()
    conn.close(); return render_template("chat_groups.html",groups=groups,social_groups=social_groups,csrf=csrf_token())


@app.route("/admin/analytics")
def admin_analytics():
    if not admin_required(): return redirect(url_for("login"))
    conn=get_db_connection()
    stats={
        "users":conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "universities":conn.execute("SELECT COUNT(*) FROM universities").fetchone()[0],
        "departments":conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0],
        "batches":conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0],
        "groups":conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0],
        "clubs":conn.execute("SELECT COUNT(*) FROM clubs").fetchone()[0],
        "events":conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        "posts":conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
        "messages":conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
        "unread_messages":conn.execute("SELECT COUNT(*) FROM messages WHERE is_read=0").fetchone()[0],
        "storage_posts":conn.execute("SELECT COUNT(*) FROM posts WHERE media_token IS NOT NULL").fetchone()[0],
    }
    university_stats=conn.execute("SELECT u.name,COUNT(x.id) users,COUNT(DISTINCT d.id) departments FROM universities u LEFT JOIN users x ON x.university_id=u.id LEFT JOIN departments d ON d.university_id=u.id GROUP BY u.id ORDER BY users DESC").fetchall()
    conn.close(); return render_template("analytics.html",stats=stats,university_stats=university_stats)

@app.route("/admin/backup")
def admin_backup():
    if not admin_required(): return redirect(url_for("login"))
    if USE_POSTGRES:
        return jsonify({
            "error": "PostgreSQL is active. Use your managed PostgreSQL backup/export facility instead of downloading database.db."
        }), 501
    from flask import send_file
    db_path=DB_FILE
    if not os.path.isfile(db_path): abort(404)
    return send_file(db_path,as_attachment=True,download_name="university_connect_backup.sqlite3",mimetype="application/octet-stream")


# ---------------------------------------------------------
# Dashboard
# ---------------------------------------------------------

@app.route("/")
def landing():
    """Public entry point: always open the user portal first.

    The admin panel is deliberately not linked from the public UI.
    Administrators must use the dedicated admin login route.
    """
    if user_required():
        return redirect(url_for("user_home"))
    return redirect(url_for("user_login"))


@app.route("/admin", methods=["GET", "POST"])
def home():

    # Admin dashboard is never public.
    if not admin_required():
        return redirect(url_for("login"))

    conn = get_db_connection()

    # -----------------------------------------------------
    # Register user
    # -----------------------------------------------------

    if request.method == "POST":

        require_csrf()
        if request.form.get("form_type") == "site_settings":
            new_site_name = request.form.get("site_name", "").strip()
            if not new_site_name or len(new_site_name) > 80:
                flash("Website name must be between 1 and 80 characters.", "error")
            else:
                conn.execute("UPDATE site_settings SET value=? WHERE key='site_name'", (new_site_name,))
                conn.commit()
                log_activity("SITE_NAME_CHANGE", f"Changed website name to: {new_site_name}")
                flash("Website name updated successfully.", "success")
            conn.close()
            return redirect(url_for("home"))

        name = request.form.get("name", "").strip()
        gmail = request.form.get("gmail", "").strip().lower()
        username = request.form.get("username", "").strip().lower()
        phone = normalize_phone(request.form.get("phone", ""))
        password = request.form.get("password", "")
        file = request.files.get("photo")

        if not name or not gmail or not username or not phone or not password or not file:
            flash("Name, Gmail, password and profile photo are required.", "error")

        elif not valid_gmail(gmail):
            flash("Please enter a valid Gmail address ending in @gmail.com.", "error")

        elif not valid_phone(phone):
            flash("Please enter a valid international phone number with country code.", "error")

        elif not allowed_file(file.filename):
            flash("Invalid profile image format.", "error")

        else:

            filename = generate_unique_filename(
                file.filename,
                prefix="profile"
            )

            if filename:

                duplicate = conn.execute(
                    "SELECT id FROM users WHERE lower(gmail) = ? OR lower(username) = ? OR phone = ?",
                    (gmail, username, phone)
                ).fetchone()

                if duplicate:
                    conn.close()
                    flash("That Gmail or username is already registered.", "error")
                    return redirect(url_for("home"))

                file.save(
                    os.path.join(
                        app.config["UPLOAD_FOLDER"],
                        filename
                    )
                )

                cursor = conn.execute(
                    """
                    INSERT INTO users
                    (
                        name,
                        gmail,
                        phone,
                        photo,
                        username,
                        password_hash
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        gmail,
                        phone,
                        filename,
                        username,
                        generate_password_hash(password)
                    )
                )

                user_id = cursor.lastrowid

                conn.commit()

                conn.close()

                log_activity(
                    "REGISTER",
                    f"Registered new profile: {name} ({gmail})",
                    user_id
                )

                flash(
                    f"Profile for {name} created successfully.",
                    "success"
                )

                return redirect(url_for("home"))

    # -----------------------------------------------------
    # Search/filter
    # -----------------------------------------------------

    search_query = request.args.get(
        "search",
        ""
    ).strip()

    status_filter = request.args.get(
        "status",
        ""
    ).strip()

    sql_base = "SELECT * FROM users WHERE 1=1"

    params = []

    if search_query:

        sql_base += """
            AND (
                name LIKE ?
                OR gmail LIKE ?
                OR username LIKE ?
                OR nickname LIKE ?
            )
        """

        search_value = f"%{search_query}%"

        params.extend([
            search_value,
            search_value,
            search_value,
            search_value
        ])

    if status_filter:

        sql_base += """
            AND relationship_status = ?
        """

        params.append(status_filter)

    sql_base += " ORDER BY id DESC"

    stored_profiles = conn.execute(
        sql_base,
        params
    ).fetchall()

    total_users = len(stored_profiles)

    conn.close()

    return render_template(
        "index.html",
        profiles=stored_profiles,
        total_users=total_users,
        search_query=search_query,
        status_filter=status_filter
    )


# ---------------------------------------------------------
# Admin user management
# ---------------------------------------------------------

@app.route("/admin/users")
def admin_users():
    if not admin_required():
        return redirect(url_for("login"))

    query = request.args.get("q", "").strip()
    conn = get_db_connection()
    if query:
        like = f"%{query}%"
        users = conn.execute(
            """SELECT u.*, COALESCE(r.name, 'STUDENT') AS role_name,
                      un.name AS university_name, d.name AS department_name, b.name AS batch_name
               FROM users u
               LEFT JOIN user_roles ur ON ur.user_id = u.id
               LEFT JOIN roles r ON r.id = ur.role_id
               LEFT JOIN universities un ON un.id = u.university_id
               LEFT JOIN departments d ON d.id = u.department_id
               LEFT JOIN batches b ON b.id = u.batch_id
               WHERE u.name LIKE ? OR u.username LIKE ? OR u.gmail LIKE ? OR u.phone LIKE ?
               ORDER BY u.id DESC""",
            (like, like, like, like)
        ).fetchall()
    else:
        users = conn.execute(
            """SELECT u.*, COALESCE(r.name, 'STUDENT') AS role_name,
                      un.name AS university_name, d.name AS department_name, b.name AS batch_name
               FROM users u
               LEFT JOIN user_roles ur ON ur.user_id = u.id
               LEFT JOIN roles r ON r.id = ur.role_id
               LEFT JOIN universities un ON un.id = u.university_id
               LEFT JOIN departments d ON d.id = u.department_id
               LEFT JOIN batches b ON b.id = u.batch_id
               ORDER BY u.id DESC"""
        ).fetchall()
    universities=conn.execute("SELECT id,name FROM universities ORDER BY name").fetchall()
    departments=conn.execute("SELECT id,university_id,name FROM departments ORDER BY name").fetchall()
    batches=conn.execute("SELECT id,university_id,department_id,name FROM batches ORDER BY name").fetchall()
    roles=conn.execute("SELECT name FROM roles ORDER BY id").fetchall()
    conn.close()
    return render_template("users.html", users=users, query=query, universities=universities, departments=departments, batches=batches, roles=roles)


@app.route("/admin/users/<int:user_id>/organization", methods=["POST"])
def admin_assign_organization(user_id):
    if not admin_required(): return redirect(url_for("login"))
    require_csrf(); conn=get_db_connection()
    user=conn.execute("SELECT id FROM users WHERE id=?",(user_id,)).fetchone()
    if not user:
        conn.close(); flash("User not found.","error"); return redirect(url_for("admin_users"))
    university_id=request.form.get("university_id",type=int) or None
    department_id=request.form.get("department_id",type=int) or None
    batch_id=request.form.get("batch_id",type=int) or None
    role_name=request.form.get("role_name","STUDENT").strip()
    try:
        if university_id and not conn.execute("SELECT 1 FROM universities WHERE id=?",(university_id,)).fetchone():
            raise ValueError("Invalid university selected.")
        if department_id:
            dep=conn.execute("SELECT university_id FROM departments WHERE id=?",(department_id,)).fetchone()
            if not dep or dep["university_id"] != university_id:
                raise ValueError("Department does not belong to the selected university.")
        if batch_id:
            batch=conn.execute("SELECT university_id,department_id FROM batches WHERE id=?",(batch_id,)).fetchone()
            if not batch or batch["university_id"] != university_id or (batch["department_id"] and batch["department_id"] != department_id):
                raise ValueError("Batch does not match the selected university/department.")
        role=conn.execute("SELECT id FROM roles WHERE name=?",(role_name,)).fetchone()
        if not role:
            raise ValueError("Invalid role selected.")
        conn.execute("UPDATE users SET university_id=?,department_id=?,batch_id=? WHERE id=?",(university_id,department_id,batch_id,user_id))
        conn.execute("DELETE FROM user_roles WHERE user_id=?",(user_id,))
        conn.execute("INSERT INTO user_roles(user_id,role_id) VALUES(?,?)",(user_id,role["id"]))
        notify_user(conn,user_id,"Organization updated",f"Your university/department/batch and role were updated to {role_name}.")
        conn.commit(); conn.close(); flash("University, department, batch and role updated.","success")
    except Exception as e:
        conn.rollback(); conn.close(); flash(f"Could not update organization: {e}","error")
    return redirect(url_for("admin_users"))

@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
def admin_delete_user(user_id):
    if not admin_required():
        return redirect(url_for("login"))
    require_csrf()
    conn = get_db_connection()
    user = conn.execute("SELECT name, gmail FROM users WHERE id=?", (user_id,)).fetchone()
    if session.get("admin_user_id") and int(session["admin_user_id"]) == user_id:
        conn.close(); flash("You cannot delete the administrator account currently in use.", "error"); return redirect(url_for("admin_users"))
    if not user:
        conn.close()
        flash("User not found.", "error")
        return redirect(url_for("admin_users"))
    post_media = conn.execute("SELECT media_token, original_name FROM posts WHERE user_id=?", (user_id,)).fetchall()
    story_media = conn.execute("SELECT media_token, original_name FROM stories WHERE user_id=?", (user_id,)).fetchall()
    for item in [*post_media, *story_media]:
        remove_private_media(item["media_token"], item["original_name"])
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    log_activity("DELETE_USER", f"Deleted user: {user['name']} ({user['gmail']})", user_id)
    flash("User deleted successfully.", "success")
    return redirect(url_for("admin_users"))


# ---------------------------------------------------------
# View / edit profile
# ---------------------------------------------------------

@app.route(
    "/user/<int:user_id>",
    methods=["GET", "POST"]
)
def view_profile(user_id):

    if not admin_required():
        return redirect(url_for("login"))

    conn = get_db_connection()

    profile = conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not profile:

        conn.close()

        return "<h1>User profile not found</h1>", 404

    # -----------------------------------------------------
    # POST actions
    # -----------------------------------------------------

    if request.method == "POST":

        require_csrf()
        form_identifier = request.form.get(
            "form_type"
        )

        # -------------------------------------------------
        # Password reset by admin
        # -------------------------------------------------

        if form_identifier == "password_reset":
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            if len(new_password) < 6:
                flash("Password must be at least 6 characters.", "error")
            elif new_password != confirm_password:
                flash("Passwords do not match.", "error")
            else:
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(new_password), user_id)
                )
                conn.commit()
                log_activity(
                    "PASSWORD_RESET",
                    f"Reset login password for profile '{profile['name']}'.",
                    user_id
                )
                flash("User login password updated successfully.", "success")

        # -------------------------------------------------
        # Personal information update
        # -------------------------------------------------

        elif form_identifier in {"personal_info", "public_info"}:
            username = request.form.get("username", "").strip().lower()
            name = request.form.get("name", profile["name"]).strip()
            gmail = request.form.get("gmail", profile["gmail"]).strip().lower()
            age = request.form.get("age") or None
            fields = {
                "name": name, "gmail": gmail, "username": username, "age": age,
                "nickname": request.form.get("nickname", "").strip(),
                "partner": request.form.get("partner", "").strip(),
                "relationship_status": request.form.get("relationship_status", "Single").strip(),
                "phone": normalize_phone(request.form.get("phone", profile["phone"] or "")),
                "location": request.form.get("location", profile["location"] or "").strip(),
                "birth_date": request.form.get("birth_date", profile["birth_date"] or "").strip(),
                "headline": request.form.get("headline", profile["headline"] or "").strip(),
                "occupation": request.form.get("occupation", profile["occupation"] or "").strip(),
                "company": request.form.get("company", profile["company"] or "").strip(),
                "website": request.form.get("website", profile["website"] or "").strip(),
                "bio": request.form.get("bio", profile["bio"] or "").strip(),
                "education": request.form.get("education", profile["education"] or "").strip(),
                "skills": request.form.get("skills", profile["skills"] or "").strip(),
                "experience": request.form.get("experience", profile["experience"] or "").strip(),
                "achievements": request.form.get("achievements", profile["achievements"] or "").strip(),
                "interests": request.form.get("interests", profile["interests"] or "").strip(),
                "projects": request.form.get("projects", profile["projects"] or "").strip(),
                "certifications": request.form.get("certifications", profile["certifications"] or "").strip(),
                "references_text": request.form.get("references_text", profile["references_text"] or "").strip(),
                "career_objective": request.form.get("career_objective", profile["career_objective"] or "").strip(),
                "study_status": request.form.get("study_status", profile["study_status"] or "").strip(),
                "department": request.form.get("department", profile["department"] or "").strip(),
                "academic_year": request.form.get("academic_year", profile["academic_year"] or "").strip(),
                "semester": request.form.get("semester", profile["semester"] or "").strip(),
                "student_id": request.form.get("student_id", profile["student_id"] or "").strip(),
                "university": request.form.get("university", profile["university"] or "").strip(),
                "profile_view": request.form.get("profile_view", profile["profile_view"] or "facebook").strip().lower()
            }
            if fields["profile_view"] not in ("facebook", "cv"):
                fields["profile_view"] = "facebook"
            if not fields["name"] or not fields["username"] or not fields["gmail"]:
                flash("Name, username and Gmail are required.", "error")
            elif not valid_gmail(fields["gmail"]):
                flash("Please enter a valid Gmail address ending in @gmail.com.", "error")
            elif fields["phone"] and not valid_phone(fields["phone"]):
                flash("Please enter a valid international phone number.", "error")
            elif conn.execute("SELECT id FROM users WHERE (lower(username)=? OR lower(gmail)=? OR phone=?) AND id!=?", (fields["username"], fields["gmail"], fields["phone"], user_id)).fetchone():
                flash("That username, Gmail or phone number is already in use.", "error")
            else:
                keys=list(fields)
                old_values={k:profile[k] for k in keys}
                try:
                    conn.execute("UPDATE users SET " + ",".join(f"{k}=?" for k in keys) + " WHERE id=?", tuple(fields[k] for k in keys)+(user_id,))
                    conn.commit()
                    changes=[f"{k}: '{old_values[k]}' → '{fields[k]}'" for k in keys if old_values[k]!=fields[k]]
                    if changes:
                        log_activity("UPDATE", f"Updated public profile '{profile['name']}'. Changes: {'; '.join(changes)}", user_id)
                        flash("Public profile information updated successfully.", "success")
                    else:
                        flash("No profile information was changed.", "info")
                except Exception as exc:
                    conn.rollback()
                    flash(f"Profile update failed: {exc}", "error")

        elif form_identifier == "admin_media":
            photo=request.files.get("photo"); cover=request.files.get("cover_photo"); changed=[]
            try:
                if photo and photo.filename:
                    if not allowed_file(photo.filename) or not validate_image_signature(photo): raise ValueError("Invalid profile image format.")
                    new_name=generate_unique_filename(photo.filename,prefix="profile"); new_path=os.path.join(app.config["UPLOAD_FOLDER"],new_name); photo.save(new_path)
                    old_photo=profile["photo"]; conn.execute("UPDATE users SET photo=? WHERE id=?",(new_name,user_id)); changed.append("profile photo")
                    if old_photo and old_photo!="default_profile.png":
                        try: os.remove(os.path.join(app.config["UPLOAD_FOLDER"],old_photo))
                        except OSError: pass
                if cover and cover.filename:
                    if not allowed_file(cover.filename) or not validate_image_signature(cover): raise ValueError("Invalid cover photo format.")
                    new_cover=generate_unique_filename(cover.filename,prefix="cover"); cover_path=os.path.join(app.config["UPLOAD_FOLDER"],new_cover); cover.save(cover_path)
                    old_cover=profile["cover_photo"]; conn.execute("UPDATE users SET cover_photo=? WHERE id=?",(new_cover,user_id)); create_media_update_post(conn,user_id,cover_path,"updated their cover photo."); changed.append("cover photo")
                    if old_cover and old_cover!=new_cover:
                        try: os.remove(os.path.join(app.config["UPLOAD_FOLDER"],old_cover))
                        except OSError: pass
                conn.commit()
                flash("Profile media updated successfully.","success") if changed else flash("No media was selected.","info")
                if changed: log_activity("UPDATE_MEDIA",f"Updated {', '.join(changed)} for profile '{profile['name']}'.",user_id)
            except Exception as exc:
                conn.rollback(); flash(f"Profile media update failed: {exc}","error")

        # -------------------------------------------------
        # Gallery upload
        # -------------------------------------------------

        elif form_identifier == "gallery_upload":

            gallery_files = request.files.getlist(
                "gallery_photos"
            )

            uploaded_count = 0

            for file in gallery_files:

                if not file or not file.filename:
                    continue

                if not allowed_file(file.filename) or not validate_image_signature(file):
                    continue

                filename = generate_unique_filename(
                    file.filename,
                    prefix=f"gallery_{user_id}"
                )

                if not filename:
                    continue

                file.save(
                    os.path.join(
                        app.config["UPLOAD_FOLDER"],
                        filename
                    )
                )

                conn.execute(
                    """
                    INSERT INTO gallery
                    (
                        user_id,
                        image_path
                    )
                    VALUES (?, ?)
                    """,
                    (
                        user_id,
                        filename
                    )
                )

                uploaded_count += 1

            conn.commit()

            if uploaded_count:

                log_activity(
                    "GALLERY_UPLOAD",
                    (
                        f"Uploaded {uploaded_count} "
                        f"gallery image(s) for "
                        f"profile '{profile['name']}'."
                    ),
                    user_id
                )

                flash(
                    f"{uploaded_count} photo(s) uploaded.",
                    "success"
                )

            else:

                flash(
                    "No valid images were uploaded.",
                    "error"
                )

        conn.close()

        return redirect(
            url_for(
                "view_profile",
                user_id=user_id
            )
        )

    # -----------------------------------------------------
    # Gallery
    # -----------------------------------------------------

    gallery_items = conn.execute(
        """
        SELECT *
        FROM gallery
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (user_id,)
    ).fetchall()

    conn.close()

    return render_template(
        "profile.html",
        profile=profile,
        gallery=gallery_items
    )


# ---------------------------------------------------------
# Delete individual gallery image
# ---------------------------------------------------------

@app.route(
    "/gallery/delete/<int:image_id>",
    methods=["POST"]
)
def delete_gallery_image(image_id):

    if not admin_required():
        return redirect(url_for("login"))

    require_csrf()
    conn = get_db_connection()

    image = conn.execute(
        """
        SELECT
            gallery.id,
            gallery.user_id,
            gallery.image_path,
            users.name
        FROM gallery
        JOIN users
            ON users.id = gallery.user_id
        WHERE gallery.id = ?
        """,
        (image_id,)
    ).fetchone()

    if not image:

        conn.close()

        flash(
            "Gallery image was not found.",
            "error"
        )

        return redirect(url_for("home"))

    user_id = image["user_id"]
    user_name = image["name"]
    image_path = image["image_path"]

    # Delete physical file.
    try:

        os.remove(
            os.path.join(
                app.config["UPLOAD_FOLDER"],
                image_path
            )
        )

    except OSError:
        pass

    # Delete database record.
    conn.execute(
        "DELETE FROM gallery WHERE id = ?",
        (image_id,)
    )

    conn.commit()
    conn.close()

    log_activity(
        "DELETE_GALLERY_IMAGE",
        (
            f"Deleted gallery image "
            f"'{image_path}' from "
            f"profile '{user_name}'."
        ),
        user_id
    )

    flash(
        "Gallery photo deleted successfully.",
        "success"
    )

    return redirect(
        url_for(
            "view_profile",
            user_id=user_id
        )
    )


# ---------------------------------------------------------
# Delete complete user profile
# ---------------------------------------------------------

@app.route(
    "/delete/<int:user_id>",
    methods=["POST"]
)
def delete_user(user_id):

    if not admin_required():
        return redirect(url_for("login"))

    require_csrf()
    conn = get_db_connection()

    user = conn.execute(
        """
        SELECT *
        FROM users
        WHERE id = ?
        """,
        (user_id,)
    ).fetchone()

    if not user:

        conn.close()

        flash(
            "Profile not found.",
            "error"
        )

        return redirect(url_for("home"))

    user_name = user["name"]
    user_email = user["gmail"]

    # -----------------------------------------------------
    # Delete profile picture
    # -----------------------------------------------------

    if user["photo"]:

        try:

            os.remove(
                os.path.join(
                    app.config["UPLOAD_FOLDER"],
                    user["photo"]
                )
            )

        except OSError:
            pass

    # -----------------------------------------------------
    # Delete gallery files
    # -----------------------------------------------------

    gallery_items = conn.execute(
        """
        SELECT image_path
        FROM gallery
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchall()

    for item in gallery_items:

        try:

            os.remove(
                os.path.join(
                    app.config["UPLOAD_FOLDER"],
                    item["image_path"]
                )
            )

        except OSError:
            pass

    post_media = conn.execute("SELECT media_token, original_name FROM posts WHERE user_id=?", (user_id,)).fetchall()
    story_media = conn.execute("SELECT media_token, original_name FROM stories WHERE user_id=?", (user_id,)).fetchall()
    for item in [*post_media, *story_media]:
        remove_private_media(item["media_token"], item["original_name"])

    # -----------------------------------------------------
    # Delete database records
    # -----------------------------------------------------

    conn.execute(
        "DELETE FROM gallery WHERE user_id = ?",
        (user_id,)
    )

    conn.execute(
        "DELETE FROM users WHERE id = ?",
        (user_id,)
    )

    conn.commit()
    conn.close()

    log_activity(
        "DELETE",
        (
            f"Deleted profile "
            f"'{user_name}' ({user_email}) "
            f"and all associated gallery images."
        ),
        user_id
    )

    flash(
        f"Profile '{user_name}' deleted.",
        "success"
    )

    return redirect(
        url_for("home")
    )


# ---------------------------------------------------------
# Activity Log
# ---------------------------------------------------------

@app.route("/activity-log")
def activity_log():

    if not admin_required():
        return redirect(url_for("login"))

    conn = get_db_connection()

    logs = conn.execute(
        """
        SELECT *
        FROM activity_log
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "activity_log.html",
        logs=logs
    )


# ---------------------------------------------------------
# Error pages
# ---------------------------------------------------------

@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, title="Bad request", message=getattr(error, "description", "The request could not be processed.")), 400


@app.errorhandler(403)
def forbidden(error):
    return render_template("error.html", code=403, title="Access denied", message="You do not have permission to access this page."), 403


@app.errorhandler(404)
def not_found(error):
    return render_template("error.html", code=404, title="Page not found", message="The page you requested does not exist."), 404


@app.errorhandler(413)
def too_large(error):
    return render_template("error.html", code=413, title="File too large", message="The uploaded file is larger than the allowed limit."), 413


@app.errorhandler(500)
def server_error(error):
    return render_template("error.html", code=500, title="Server error", message="An unexpected error occurred."), 500


# ---------------------------------------------------------
# Run
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=False)
