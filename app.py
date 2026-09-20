import os
import sqlite3
from sqlite3 import IntegrityError
import uuid
import secrets
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
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

# Development-only ephemeral key. It invalidates sessions after restart.
app.secret_key = FLASK_SECRET_KEY or secrets.token_hex(32)

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
REQUIRE_HTTPS = os.environ.get("REQUIRE_HTTPS", "0") == "1"

DB_FILE = "database.db"
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

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    # Enable foreign-key support for every connection.
    conn.execute("PRAGMA foreign_keys = ON")

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
        "birth_date": "TEXT DEFAULT ''"
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
            is_read INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (receiver_id) REFERENCES users(id) ON DELETE CASCADE
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            caption TEXT DEFAULT '',
            media_token TEXT NOT NULL UNIQUE,
            media_type TEXT NOT NULL CHECK(media_type IN ('image','video')),
            original_name TEXT DEFAULT '',
            created_at TEXT NOT NULL,
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


@app.context_processor
def inject_site_settings():
    return {"site_name": get_site_name()}

def require_csrf():
    token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not token or not secrets.compare_digest(token, session.get("csrf_token", "")):
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
            session["logged_in"] = True
            session["admin_username"] = username

            return redirect(url_for("home"))

        error = "Invalid admin credentials. Access denied."

    return render_template("login.html", error=error)


# ---------------------------------------------------------
# Logout
# ---------------------------------------------------------

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))



# ---------------------------------------------------------
# Public user registration
# ---------------------------------------------------------

def normalize_phone(value):
    return "".join(value.strip().split())


def valid_phone(value):
    import re
    return bool(re.fullmatch(r"\+8801[3-9]\d{8}", value))


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
            flash("Phone number must be a valid Bangladesh number starting with +880 (example: +8801712345678).", "error")
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
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; img-src 'self' data:; media-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
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
            session.clear()
            session["user_logged_in"] = True
            session["user_id"] = user["id"]
            return redirect(url_for("user_home"))
        error = "Invalid username, Gmail, phone number or password."
    return render_template("user_login.html", error=error)


@app.route("/user-logout")
def user_logout():
    session.clear()
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
        session.clear()
        return redirect(url_for("user_login"))

    # People section: searchable so users do not need a separate page.
    if search:
        users = conn.execute(
            """
            SELECT id, name, gmail, photo, username, nickname, relationship_status
            FROM users
            WHERE id != ?
              AND (name LIKE ? OR username LIKE ? OR gmail LIKE ? OR nickname LIKE ?)
            ORDER BY name COLLATE NOCASE
            """,
            (user_id, f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%")
        ).fetchall()
    else:
        users = conn.execute(
            """
            SELECT id, name, gmail, photo, username, nickname, relationship_status
            FROM users
            WHERE id != ?
            ORDER BY name COLLATE NOCASE
            """,
            (user_id,)
        ).fetchall()

    unread_count = conn.execute(
        """
        SELECT COUNT(*) FROM messages
        WHERE receiver_id = ? AND is_read = 0
        """,
        (user_id,)
    ).fetchone()[0]

    total_members = conn.execute(
        "SELECT COUNT(*) FROM users WHERE id != ?",
        (user_id,)
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

    posts = fetch_feed(conn, user_id, 0, 8)
    feed_posts = serialize_posts(conn, posts, user_id)
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
        csrf=csrf_token()
    )


@app.route("/feed")
def feed():
    if not user_required():
        return redirect(url_for("user_login"))
    return redirect(url_for("user_home") + "#newsfeed")


def fetch_feed(conn, user_id, offset=0, limit=8):
    return conn.execute(
        """
        SELECT p.*, u.name, u.username, u.photo,
               (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id=p.id) AS like_count,
               (SELECT COUNT(*) FROM post_comments pc WHERE pc.post_id=p.id) AS comment_count,
               EXISTS(SELECT 1 FROM post_likes me WHERE me.post_id=p.id AND me.user_id=?) AS liked_by_me
        FROM posts p
        JOIN users u ON u.id=p.user_id
        ORDER BY p.id DESC
        LIMIT ? OFFSET ?
        """, (user_id, limit, offset)
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
            "id": p["id"], "name": p["name"], "username": p["username"], "photo": p["photo"],
            "caption": p["caption"], "media_type": p["media_type"],
            "media_url": url_for("private_post_media", token=p["media_token"]),
            "created_at": p["created_at"], "like_count": p["like_count"],
            "comment_count": p["comment_count"], "liked_by_me": bool(p["liked_by_me"]),
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


@app.route("/posts/create", methods=["POST"])
def create_post():
    if not user_required():
        return redirect(url_for("user_login"))
    require_csrf()
    caption=request.form.get("caption", "").strip()
    media=request.files.get("media")
    if not media or not media.filename:
        flash("Choose a photo or short reel video.", "error")
        return redirect(url_for("user_home")+"#newsfeed")
    original=secure_filename(media.filename)
    ext=original.rsplit(".",1)[-1].lower() if "." in original else ""
    if ext not in POST_IMAGE_EXTENSIONS|POST_VIDEO_EXTENSIONS:
        flash("Unsupported media format.", "error")
        return redirect(url_for("user_home")+"#newsfeed")
    media_type="video" if ext in POST_VIDEO_EXTENSIONS else "image"
    token=secrets.token_urlsafe(36)
    filename=token+"."+ext
    media.save(os.path.join(PRIVATE_MEDIA_FOLDER, filename))
    conn=get_db_connection()
    conn.execute("INSERT INTO posts(user_id,caption,media_token,media_type,original_name,created_at) VALUES(?,?,?,?,?,?)",
                 (session["user_id"], caption, token, media_type, original, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit(); conn.close()
    flash("Posted to the student newsfeed.", "success")
    return redirect(url_for("user_home")+"#newsfeed")


@app.route("/post/<int:post_id>/like", methods=["POST"])
def toggle_post_like(post_id):
    if not user_required(): return jsonify({"error":"login_required"}),401
    require_csrf()
    conn=get_db_connection(); uid=session["user_id"]
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
    conn=get_db_connection(); exists=conn.execute("SELECT 1 FROM posts WHERE id=?",(post_id,)).fetchone()
    if not exists: conn.close(); return jsonify({"error":"Post not found"}),404
    conn.execute("INSERT INTO post_comments(post_id,user_id,comment,created_at) VALUES(?,?,?,?)",(post_id,session["user_id"],comment,datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
    count=conn.execute("SELECT COUNT(*) FROM post_comments WHERE post_id=?",(post_id,)).fetchone()[0]
    conn.commit(); conn.close()
    return jsonify({"comment_count":count})


@app.route("/private-post-media/<token>")
def private_post_media(token):
    if not user_required(): abort(401)
    conn=get_db_connection(); post=conn.execute("SELECT * FROM posts WHERE media_token=?",(token,)).fetchone(); conn.close()
    if not post: abort(404)
    path=os.path.join(PRIVATE_MEDIA_FOLDER, token+"."+post["original_name"].rsplit(".",1)[-1].lower())
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
    conn.close()

    if not profile:
        session.clear()
        return redirect(url_for("user_login"))

    return render_template("detailed_profile.html", profile=profile, gallery=gallery, is_own=True)


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
        session.clear()
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
                flash("Phone number must start with +880 and use a valid Bangladesh mobile format.", "error")
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
            study_status = request.form.get("study_status", "").strip()
            department = request.form.get("department", "").strip()
            academic_year = request.form.get("academic_year", "").strip()
            semester = request.form.get("semester", "").strip()
            student_id = request.form.get("student_id", "").strip()
            university = request.form.get("university", "").strip()
            photo = request.files.get("photo")

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
            if photo and photo.filename:
                if not allowed_file(photo.filename) or not validate_image_signature(photo):
                    flash("Invalid profile image format.", "error")
                    conn.close()
                    return redirect(url_for("profile_settings"))
                new_filename = generate_unique_filename(photo.filename, prefix="profile")
                if new_filename:
                    photo.save(os.path.join(app.config["UPLOAD_FOLDER"], new_filename))
                    if filename and filename != "default_profile.png":
                        try:
                            os.remove(os.path.join(app.config["UPLOAD_FOLDER"], filename))
                        except OSError:
                            pass
                    filename = new_filename

            conn.execute(
                """
                UPDATE users SET
                    name=?, gmail=?, username=?, photo=?, nickname=?, age=?,
                    relationship_status=?, partner=?, phone=?, location=?, birth_date=?,
                    headline=?, occupation=?, company=?, website=?, bio=?, education=?,
                    skills=?, experience=?, achievements=?, interests=?,
                    study_status=?, department=?, academic_year=?, semester=?, student_id=?, university=?
                WHERE id=?
                """,
                (name, gmail, username, filename, nickname, age or None,
                 relationship_status or "Single", partner, phone, location, birth_date,
                 headline, occupation, company, website, bio, education, skills,
                 experience, achievements, interests, study_status, department, academic_year, semester, student_id, university, user_id)
            )
            conn.commit()
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
    conn.close()

    if not profile:
        return "<h1>Profile not found</h1>", 404

    return render_template("detailed_profile.html", profile=profile, gallery=gallery, is_own=(user_id == session["user_id"]))


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

    conn.close()

    return render_template(
        "messages.html",
        current_user=current_user,
        conversations=conversations,
        users=users
    )


@app.route("/messages/<int:user_id>")
def chat(user_id):
    if not user_required():
        return redirect(url_for("user_login"))

    current_user_id = session["user_id"]

    if user_id == current_user_id:
        return redirect(url_for("messages"))

    conn = get_db_connection()

    other_user = conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not other_user:
        conn.close()
        return "<h1>User not found</h1>", 404

    conn.execute(
        """
        UPDATE messages
        SET is_read = 1
        WHERE sender_id = ? AND receiver_id = ?
        """,
        (user_id, current_user_id)
    )
    conn.commit()

    chat_messages = conn.execute(
        """
        SELECT m.*, sender.name AS sender_name
        FROM messages m
        JOIN users sender ON sender.id = m.sender_id
        WHERE (m.sender_id = ? AND m.receiver_id = ?)
           OR (m.sender_id = ? AND m.receiver_id = ?)
        ORDER BY m.id ASC
        """,
        (current_user_id, user_id, user_id, current_user_id)
    ).fetchall()

    current_user = conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (current_user_id,)
    ).fetchone()

    conn.close()

    return render_template(
        "chat.html",
        current_user=current_user,
        other_user=other_user,
        chat_messages=chat_messages
    )


@app.route("/messages/send", methods=["POST"])
def send_message():
    if not user_required():
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"error": "login_required"}), 401
        return redirect(url_for("user_login"))

    require_csrf()
    sender_id = session["user_id"]
    receiver_id = request.form.get("receiver_id", type=int)
    message_text = request.form.get("message", "").strip()

    if not receiver_id or not message_text:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"error": "Please enter a message."}), 400
        flash("Please enter a message.", "error")
        return redirect(url_for("messages"))

    if receiver_id == sender_id:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"error": "You cannot message yourself."}), 400
        flash("You cannot message yourself.", "error")
        return redirect(url_for("messages"))

    conn = get_db_connection()
    recipient = conn.execute(
        "SELECT id FROM users WHERE id = ?",
        (receiver_id,)
    ).fetchone()

    if not recipient:
        conn.close()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"error": "Recipient not found."}), 404
        flash("Recipient not found.", "error")
        return redirect(url_for("messages"))

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        """
        INSERT INTO messages
        (sender_id, receiver_id, message, created_at, is_read)
        VALUES (?, ?, ?, ?, 0)
        """,
        (sender_id, receiver_id, message_text, created_at)
    )
    message_id = cursor.lastrowid
    conn.commit()
    conn.close()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "ok": True,
            "message": {
                "id": message_id,
                "sender_id": sender_id,
                "receiver_id": receiver_id,
                "message": message_text,
                "created_at": created_at,
            }
        })

    return redirect(url_for("chat", user_id=receiver_id))


@app.route("/api/messages/<int:user_id>")
def api_messages(user_id):
    """Return messages newer than since_id for the open conversation.

    This is intentionally lightweight polling rather than WebSockets so the
    application keeps working behind ordinary WSGI/HTTPS deployments.
    """
    if not user_required():
        return jsonify({"error": "login_required"}), 401

    current_user_id = session["user_id"]
    if user_id == current_user_id:
        return jsonify({"error": "invalid_recipient"}), 400

    try:
        since_id = max(0, int(request.args.get("since_id", 0)))
    except (TypeError, ValueError):
        since_id = 0

    conn = get_db_connection()
    if not conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
        conn.close()
        return jsonify({"error": "user_not_found"}), 404

    # Opening/polling a chat means messages from the other participant are read.
    conn.execute(
        "UPDATE messages SET is_read=1 WHERE sender_id=? AND receiver_id=?",
        (user_id, current_user_id)
    )
    rows = conn.execute(
        """
        SELECT id, sender_id, receiver_id, message, created_at
        FROM messages
        WHERE id > ?
          AND ((sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?))
        ORDER BY id ASC
        """,
        (since_id, current_user_id, user_id, user_id, current_user_id)
    ).fetchall()
    conn.commit()
    conn.close()
    return jsonify({"messages": [dict(row) for row in rows]})


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
                uid=request.form.get("university_id",type=int); did=request.form.get("department_id",type=int) or None; name=request.form.get("name","").strip(); desc=request.form.get("description","").strip(); privacy=request.form.get("privacy","university")
                if not uid or not name: raise ValueError("University and group name are required.")
                conn.execute("INSERT INTO groups(university_id,department_id,name,description,privacy,created_by,created_at) VALUES(?,?,?,?,?,?,?)",(uid,did,name,desc,privacy,None,now)); flash("Group created.","success")
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
            flash("Phone number must start with +880 and use a valid Bangladesh mobile format.", "error")

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

        elif form_identifier == "personal_info":

            username = request.form.get(
                "username",
                ""
            ).strip()

            age = request.form.get("age")

            nickname = request.form.get(
                "nickname",
                ""
            ).strip()

            partner = request.form.get(
                "partner",
                ""
            ).strip()

            status = request.form.get(
                "relationship_status",
                "Single"
            )

            old_values = {
                "username": profile["username"],
                "age": profile["age"],
                "nickname": profile["nickname"],
                "partner": profile["partner"],
                "relationship_status":
                    profile["relationship_status"]
            }

            try:
                duplicate = conn.execute("SELECT id FROM users WHERE lower(username)=? AND id!=?", (username.lower(), user_id)).fetchone() if username else None
                if duplicate:
                    raise ValueError("That username is already in use.")
                conn.execute(
                    """UPDATE users SET username=?, age=?, nickname=?, partner=?, relationship_status=? WHERE id=?""",
                    (username, age if age else None, nickname, partner, status, user_id)
                )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                flash(f"Profile update failed: {exc}", "error")
                conn.close()
                return redirect(url_for("view_profile", user_id=user_id))

            changes = []

            fields = {
                "username": username,
                "age": age if age else None,
                "nickname": nickname,
                "partner": partner,
                "relationship_status": status
            }

            for field, new_value in fields.items():

                if old_values[field] != new_value:

                    changes.append(
                        f"{field}: "
                        f"'{old_values[field]}' → "
                        f"'{new_value}'"
                    )

            if changes:

                log_activity(
                    "UPDATE",
                    (
                        f"Updated profile '{profile['name']}'. "
                        f"Changes: "
                        f"{'; '.join(changes)}"
                    ),
                    user_id
                )

                flash(
                    "Profile information updated.",
                    "success"
                )

            else:

                flash(
                    "No profile information was changed.",
                    "info"
                )

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
