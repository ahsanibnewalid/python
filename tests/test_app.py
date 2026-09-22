import os
from pathlib import Path

# Isolate the test database before importing the app.
os.environ["APP_ENV"] = "testing"
os.environ["FLASK_SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_USER"] = "admin"
from werkzeug.security import generate_password_hash
os.environ["ADMIN_PASSWORD_HASH"] = generate_password_hash("test-password")

import app


def test_login_page_loads():
    app.app.config["TESTING"] = True
    client = app.app.test_client()
    response = client.get("/login")
    assert response.status_code == 200
    assert b"Admin Authentication" in response.data


def test_user_registration_page_loads():
    app.app.config["TESTING"] = True
    client = app.app.test_client()
    response = client.get("/register")
    assert response.status_code == 200
    assert b"Create Account" in response.data


def test_admin_users_requires_login():
    app.app.config["TESTING"] = True
    client = app.app.test_client()
    response = client.get("/admin/users")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_phone_normalization_accepts_bangladesh_local_number():
    assert app.normalize_phone("01712345678") == "+8801712345678"
    assert app.valid_phone("+8801712345678")


def test_post_schema_supports_text_and_reel_types():
    conn = app.get_db_connection()
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(posts)").fetchall()}
    conn.close()
    assert "post_type" in cols
