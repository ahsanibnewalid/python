import app
from werkzeug.security import generate_password_hash

def _user(conn, name, username, university_id):
    cur = conn.execute(
        "INSERT INTO users(name,gmail,photo,username,password_hash,university_id) VALUES(?,?,?,?,?,?)",
        (name, username + "@gmail.com", "default_profile.png", username, generate_password_hash("password"), university_id),
    )
    return cur.lastrowid

def _login(client, uid):
    with client.session_transaction() as s:
        s["user_logged_in"] = True
        s["user_id"] = uid
        s["csrf_token"] = "test-csrf"

def test_health_endpoint():
    response = app.app.test_client().get("/health")
    assert response.status_code in (200, 503)

def test_admin_flag_alone_is_not_sufficient():
    client = app.app.test_client()
    with client.session_transaction() as s:
        s["logged_in"] = True
    response = client.get("/admin/users")
    assert response.status_code == 302

def test_cross_university_profile_access_is_denied():
    conn = app.get_db_connection()
    a = _user(conn, "Tenant A", "tenant_a", 1001)
    b = _user(conn, "Tenant B", "tenant_b", 2002)
    conn.commit()
    conn.close()

    client = app.app.test_client()
    _login(client, a)
    response = client.get(f"/profile/{b}")
    assert response.status_code == 403

def test_same_university_profile_access_is_allowed():
    conn = app.get_db_connection()
    a = _user(conn, "Tenant C", "tenant_c", 3003)
    b = _user(conn, "Tenant D", "tenant_d", 3003)
    conn.commit()
    conn.close()

    client = app.app.test_client()
    _login(client, a)
    response = client.get(f"/profile/{b}")
    assert response.status_code == 200
