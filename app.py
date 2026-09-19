import os
import sqlite3
import uuid
from datetime import datetime

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash


app = Flask(__name__)

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "change-this-secret-key-in-production"
)

UPLOAD_FOLDER = os.path.join("static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

DB_FILE = "database.db"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ---------------------------------------------------------
# Admin configuration
# ---------------------------------------------------------

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")

ADMIN_PASSWORD_HASH = os.environ.get(
    "ADMIN_PASSWORD_HASH",
    generate_password_hash("password123")
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

    conn.commit()
    conn.close()


init_db()


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


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


# ---------------------------------------------------------
# Login
# ---------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():

    error = None

    if request.method == "POST":

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
# Dashboard
# ---------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def home():

    if not admin_required():
        return redirect(url_for("login"))

    conn = get_db_connection()

    # -----------------------------------------------------
    # Register user
    # -----------------------------------------------------

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        gmail = request.form.get("gmail", "").strip()
        file = request.files.get("photo")

        if not name or not gmail or not file:
            flash("Name, Gmail and profile photo are required.", "error")

        elif not allowed_file(file.filename):
            flash("Invalid profile image format.", "error")

        else:

            filename = generate_unique_filename(
                file.filename,
                prefix="profile"
            )

            if filename:

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
                        photo
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        name,
                        gmail,
                        filename
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

        form_identifier = request.form.get(
            "form_type"
        )

        # -------------------------------------------------
        # Personal information update
        # -------------------------------------------------

        if form_identifier == "personal_info":

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

            conn.execute(
                """
                UPDATE users
                SET
                    username = ?,
                    age = ?,
                    nickname = ?,
                    partner = ?,
                    relationship_status = ?
                WHERE id = ?
                """,
                (
                    username,
                    age if age else None,
                    nickname,
                    partner,
                    status,
                    user_id
                )
            )

            conn.commit()

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

                if not allowed_file(file.filename):
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
# Run
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
