import app
import admin_god
import pytest
from werkzeug.security import generate_password_hash


_INSTALLED = False


def _admin_session(client):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["admin_username"] = app.ADMIN_USER
        sess["csrf_token"] = "test-csrf"


def _cleanup():
    conn = app.get_db_connection()
    for table in ["moderator_work", "moderator_scopes", "moderation_actions", "admin_staff"]:
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM platform_notifications WHERE kind='moderation'")
    conn.execute("DELETE FROM users WHERE username IN ('hardening_mod','admin')")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def admin_routes():
    global _INSTALLED
    if not _INSTALLED:
        admin_god.install(app.app, app.get_db_connection, getattr(app, "init_db", None))
        _INSTALLED = True
    client = app.app.test_client()
    _admin_session(client)
    client.get("/admin/god")
    _cleanup()


def test_owner_can_remove_non_owner_moderator():
    conn = app.get_db_connection()
    cur = conn.execute(
        "INSERT INTO users(name,gmail,photo,username,password_hash) VALUES(?,?,?,?,?)",
        ("Hardening Moderator", "hardening_mod@gmail.com", "default_profile.png", "hardening_mod", generate_password_hash("password")),
    )
    user_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO admin_staff(user_id,username,role,status,created_by,created_at) VALUES(?,?,?,?,?,?)",
        (user_id, "hardening_mod", "moderator", "active", app.ADMIN_USER, "2026-09-30T00:00:00"),
    )
    staff_id = cur.lastrowid
    conn.execute("INSERT INTO moderator_scopes(staff_id,scope) VALUES(?,?)", (staff_id, "content"))
    conn.commit()
    conn.close()

    client = app.app.test_client()
    _admin_session(client)
    response = client.post(
        f"/admin/god/staff/{staff_id}/remove",
        data={"csrf_token": "test-csrf"},
        follow_redirects=False,
    )

    assert response.status_code == 302

    conn = app.get_db_connection()
    assert conn.execute("SELECT 1 FROM admin_staff WHERE id=?", (staff_id,)).fetchone() is None
    assert conn.execute("SELECT 1 FROM moderator_scopes WHERE staff_id=?", (staff_id,)).fetchone() is None
    conn.close()


def test_owner_identity_cannot_be_removed_from_moderator_team():
    conn = app.get_db_connection()
    cur = conn.execute(
        "INSERT INTO admin_staff(user_id,username,role,status,created_by,created_at) VALUES(?,?,?,?,?,?)",
        (None, app.ADMIN_USER, "moderator", "active", app.ADMIN_USER, "2026-09-30T00:00:00"),
    )
    staff_id = cur.lastrowid
    conn.commit()
    conn.close()

    client = app.app.test_client()
    _admin_session(client)
    response = client.post(
        f"/admin/god/staff/{staff_id}/remove",
        data={"csrf_token": "test-csrf"},
        follow_redirects=False,
    )

    assert response.status_code == 302

    conn = app.get_db_connection()
    assert conn.execute("SELECT 1 FROM admin_staff WHERE id=?", (staff_id,)).fetchone() is not None
    conn.close()


def test_health_endpoint_reports_database_health():
    client = app.app.test_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["database"] == "ok"
