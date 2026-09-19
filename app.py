import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "super_secure_secret_key_change_me_in_production"

UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

DB_FILE = "database.db"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Generate a secure cryptographic hash for your admin password
# The text password here is "password123"
ADMIN_USER = "admin"
ADMIN_PASSWORD_HASH = generate_password_hash("password123")

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    # 1. Master users table schema tracking properties
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
    
    # 2. Relational Gallery Table (One-to-Many Link mapping back to users.id)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gallery (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            image_path TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()

init_db()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        # Secure Hashed Verification match lookup
        if username == ADMIN_USER and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session['logged_in'] = True
            return redirect(url_for('home'))
        else:
            error = "Invalid admin credentials. Access Denied."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route("/", methods=["GET", "POST"])
def home():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
        
    conn = get_db_connection()

    if request.method == "POST":
        name = request.form.get("name")
        gmail = request.form.get("gmail")
        file = request.files.get("photo")
        
        if name and gmail and file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            db_filename = f"user_{filename}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], db_filename))
            
            conn.execute(
                "INSERT INTO users (name, gmail, photo) VALUES (?, ?, ?)",
                (name, gmail, db_filename)
            )
            conn.commit()
            conn.close()
            return redirect(url_for('home'))

    search_query = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip()
    
    sql_base = "SELECT * FROM users WHERE 1=1"
    params = []
    
    if search_query:
        sql_base += " AND (name LIKE ? OR gmail LIKE ?)"
        params.extend([f"%{search_query}%", f"%{search_query}%"])
        
    if status_filter:
        sql_base += " AND relationship_status = ?"
        params.append(status_filter)
        
    stored_profiles = conn.execute(sql_base, params).fetchall()
    total_users = len(stored_profiles)
    conn.close()
    
    return render_template(
        "index.html", 
        profiles=stored_profiles, 
        total_users=total_users, 
        search_query=search_query, 
        status_filter=status_filter
    )

@app.route("/user/<int:user_id>", methods=["GET", "POST"])
def view_profile(user_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    
    if request.method == "POST":
        # Form type discrimination handler checks: Personal Info vs Gallery submission
        form_identifier = request.form.get("form_type")
        
        if form_identifier == "personal_info":
            username = request.form.get("username")
            age = request.form.get("age")
            nickname = request.form.get("nickname")
            partner = request.form.get("partner")
            status = request.form.get("relationship_status")
            
            conn.execute("""
                UPDATE users 
                SET username = ?, age = ?, nickname = ?, partner = ?, relationship_status = ? 
                WHERE id = ?
            """, (username, age, nickname, partner, status, user_id))
            conn.commit()
            
        elif form_identifier == "gallery_upload":
            gallery_files = request.files.getlist("gallery_photos")
            for file in gallery_files:
                if file and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    db_filename = f"gal_{user_id}_{filename}"
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], db_filename))
                    
                    conn.execute("INSERT INTO gallery (user_id, image_path) VALUES (?, ?)", (user_id, db_filename))
            conn.commit()
            
        return redirect(url_for('view_profile', user_id=user_id))

    profile = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    # Read the individual specific media files uploaded by this target member identity context
    gallery_items = conn.execute("SELECT * FROM gallery WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    
    if not profile:
        return "<h1>User profile missing inside database records</h1>", 404
        
    return render_template("profile.html", profile=profile, gallery=gallery_items)

@app.route("/delete/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    
    # 1. Purge physical files associated with personal photo and item grid arrays
    user = conn.execute("SELECT photo FROM users WHERE id = ?", (user_id,)).fetchone()
    if user and user['photo']:
        try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], user['photo']))
        except OSError: pass
            
    gallery_items = conn.execute("SELECT image_path FROM gallery WHERE user_id = ?", (user_id,)).fetchall()
    for item in gallery_items:
        try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], item['image_path']))
        except OSError: pass
            
    # 2. Execute SQL cascaded deletions across records matrices
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.execute("DELETE FROM gallery WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('home'))

if __name__ == "__main__":
    app.run(debug=True)
