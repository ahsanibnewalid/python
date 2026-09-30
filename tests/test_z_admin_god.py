import sqlite3
from pathlib import Path

import pytest
from flask import Flask

import admin_god
import app as real_app
from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]


def _make_test_app(tmp_path):
    db_path = tmp_path / "admin_god.sqlite"

    def get_db_connection():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    test_app = Flask(
        "admin_god_test_app",
        template_folder=str(ROOT / "templates"),
    )
    test_app.secret_key = "test-admin-god-secret"
    test_app.config.update(TESTING=True)

    @test_app.route("/login")
    def login():
        return "login"

    admin_god.install(test_app, get_db_connection, None)
    return test_app, get_db_connection


def _admin_session(client):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["admin_username"] = real_app.ADMIN_USER
        sess["csrf_token"] = "test-csrf"


def _prepare_schema(get_db_connection):
    conn = get_db_connection()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            gmail TEXT,
            phone TEXT,
            photo TEXT,
            username TEXT,
            password_hash TEXT
        )"""
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def admin_test_env(tmp_path):
    flask_app, get_db_connection = _make_test_app(tmp_path)
    _prepare_schema(get_db_connection)

    client = flask_app.test_client()
    _admin_session(client)

    # First request initializes the admin_god schema before test data is added.
    response = client.get("/admin/god")
    assert response.status_code == 200

    yield client, get_db_connection


def test_owner_can_remove_non_owner_moderator(admin_test_env):
    client, get_db_connection = admin_test_env

    conn = get_db_connection()
    cur = conn.execute(
        "INSERT INTO users(name,gmail,photo,username,password_hash) VALUES(?,?,?,?,?)",
        (
            "Hardening Moderator",
            "hardening_mod@gmail.com",
            "default_profile.png",
            "hardening_mod",
            generate_password_hash("password"),
        ),
    )
    user_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO admin_staff(user_id,username,role,status,created_by,created_at) VALUES(?,?,?,?,?,?)",
        (user_id, "hardening_mod", "moderator", "active", real_app.ADMIN_USER, "2026-09-30T00:00:00"),
    )
    staff_id = cur.lastrowid
    conn.execute("INSERT INTO moderator_scopes(staff_id,scope) VALUES(?,?)", (staff_id, "content"))
    conn.commit()
    conn.close()

    response = client.post(
        f"/admin/god/staff/{staff_id}/remove",
        data={"csrf_token": "test-csrf"},
        follow_redirects=False,
    )

    assert response.status_code == 302

    conn = get_db_connection()
    assert conn.execute("SELECT 1 FROM admin_staff WHERE id=?", (staff_id,)).fetchone() is None
    assert conn.execute("SELECT 1 FROM moderator_scopes WHERE staff_id=?", (staff_id,)).fetchone() is None
    conn.close()


def test_owner_identity_cannot_be_removed_from_moderator_team(admin_test_env):
    client, get_db_connection = admin_test_env

    conn = get_db_connection()
    cur = conn.execute(
        "INSERT INTO admin_staff(user_id,username,role,status,created_by,created_at) VALUES(?,?,?,?,?,?)",
        (None, real_app.ADMIN_USER, "moderator", "active", real_app.ADMIN_USER, "2026-09-30T00:00:00"),
    )
    staff_id = cur.lastrowid
    conn.commit()
    conn.close()

    response = client.post(
        f"/admin/god/staff/{staff_id}/remove",
        data={"csrf_token": "test-csrf"},
        follow_redirects=False,
    )

    assert response.status_code == 302

    conn = get_db_connection()
    assert conn.execute("SELECT 1 FROM admin_staff WHERE id=?", (staff_id,)).fetchone() is not None
    conn.close()


def test_health_endpoint_reports_database_health():
    client = real_app.app.test_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["database"] == "ok"
