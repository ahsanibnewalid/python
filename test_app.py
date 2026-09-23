import os
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
TEST_DB = TEST_ROOT / "test_database.sqlite"
os.environ["APP_ENV"] = "testing"
os.environ["DB_FILE"] = str(TEST_DB)
os.environ["FLASK_SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_USER"] = "admin"
from werkzeug.security import generate_password_hash
os.environ["ADMIN_PASSWORD_HASH"] = generate_password_hash("test-password")
os.environ["REQUIRE_HTTPS"] = "0"
os.environ["COOKIE_SECURE"] = "0"

import app

app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)


def reset_db():
    conn = app.get_db_connection()
    # Preserve schema, remove test data in dependency-safe order.
    for table in ["chat_group_messages", "chat_group_members", "chat_groups", "post_comments", "post_likes", "posts", "stories", "messages", "user_devices", "user_keys", "user_blocks", "chat_preferences", "users"]:
        try:
            conn.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    conn.commit(); conn.close()


def setup_function(_):
    reset_db()


def create_user(conn, name, username, university_id=None):
    cur = conn.execute(
        "INSERT INTO users(name,gmail,photo,username,password_hash,university_id) VALUES(?,?,?,?,?,?)",
        (name, username + "@gmail.com", "default_profile.png", username, generate_password_hash("password"), university_id),
    )
    return cur.lastrowid


def login_user(client, user_id):
    with client.session_transaction() as sess:
        sess["user_logged_in"] = True
        sess["user_id"] = user_id
        sess["csrf_token"] = "test-csrf"


def test_login_page_loads():
    response = app.app.test_client().get("/login")
    assert response.status_code == 200
    assert b"Admin Authentication" in response.data


def test_user_registration_page_loads():
    response = app.app.test_client().get("/register")
    assert response.status_code == 200
    assert b"Create Account" in response.data


def test_admin_users_requires_login():
    response = app.app.test_client().get("/admin/users")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_phone_normalization_accepts_bangladesh_local_number():
    assert app.normalize_phone("01712345678") == "+8801712345678"
    assert app.valid_phone("+8801712345678")


def test_init_db_is_idempotent_with_existing_e2ee_column():
    # Regression test for the duplicate-column startup crash reported when an
    # older local database already contains chat_preferences.e2ee_enabled.
    app.init_db()
    app.init_db()
    conn = app.get_db_connection()
    cols = {str(row["name"]).lower() for row in conn.execute("PRAGMA table_info(chat_preferences)").fetchall()}
    conn.close()
    assert "e2ee_enabled" in cols


def test_post_schema_supports_reels():
    conn = app.get_db_connection()
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(posts)").fetchall()}
    conn.close()
    assert "post_type" in cols


def test_text_post_and_reel_creation():
    conn = app.get_db_connection(); uid = create_user(conn, "Alice", "alice", 1); conn.commit(); conn.close()
    client = app.app.test_client(); login_user(client, uid)
    r = client.post("/posts/create", data={"caption":"Hello world", "post_type":"post", "csrf_token":"test-csrf"}, follow_redirects=False)
    assert r.status_code == 302
    conn = app.get_db_connection(); row = conn.execute("SELECT * FROM posts WHERE user_id=?", (uid,)).fetchone(); conn.close()
    assert row["caption"] == "Hello world" and row["original_name"] == ""


def test_plaintext_message_is_allowed_when_e2ee_is_off():
    conn = app.get_db_connection(); a = create_user(conn, "Alice", "alice", 1); b = create_user(conn, "Bob", "bob", 1); conn.commit(); conn.close()
    client = app.app.test_client(); login_user(client, a)
    r = client.post("/messages/send", data={"receiver_id": str(b), "message":"plaintext", "csrf_token":"test-csrf"}, headers={"X-Requested-With":"XMLHttpRequest"})
    assert r.status_code == 200
    conn = app.get_db_connection(); row = conn.execute("SELECT message,encryption_version FROM messages WHERE sender_id=?", (a,)).fetchone(); conn.close()
    assert row["message"] == "plaintext" and row["encryption_version"] == 0


def test_e2ee_can_be_enabled_per_chat_when_user_has_key():
    conn = app.get_db_connection(); a = create_user(conn, "Alice", "alice", 1); b = create_user(conn, "Bob", "bob", 1); conn.commit(); conn.close()
    client = app.app.test_client(); login_user(client, a)
    key='{"kty":"EC","crv":"P-256","x":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","y":"BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"}'
    assert client.post('/api/e2ee/key', json={'public_key':key}, headers={'X-CSRF-Token':'test-csrf'}).status_code == 200
    r=client.post('/api/chat/%s/preferences' % b, json={'e2ee_enabled':True}, headers={'X-CSRF-Token':'test-csrf'})
    assert r.status_code == 200 and r.get_json()['e2ee_enabled'] is True


def test_device_cannot_be_reassigned():
    conn = app.get_db_connection(); a = create_user(conn, "Alice", "alice", 1); b = create_user(conn, "Bob", "bob", 1); conn.commit(); conn.close()
    client = app.app.test_client(); login_user(client, a)
    payload={"device_id":"device-12345","identity_public_key":"A"*50}
    assert client.post("/api/e2ee/device", json=payload, headers={"X-CSRF-Token":"test-csrf"}).status_code == 200
    login_user(client, b)
    assert client.post("/api/e2ee/device", json=payload, headers={"X-CSRF-Token":"test-csrf"}).status_code == 409


def test_expired_stories_are_cleaned():
    conn = app.get_db_connection(); uid = create_user(conn, "Alice", "alice", 1); conn.execute("INSERT INTO stories(user_id,media_token,media_type,original_name,caption,created_at,expires_at) VALUES(?,?,?,?,?,?,?)", (uid,"expired-token","image","x.jpg","","2000-01-01 00:00:00","2000-01-02 00:00:00")); conn.commit(); conn.close()
    conn = app.get_db_connection(); stories=app.fetch_stories(conn,uid); row=conn.execute("SELECT * FROM stories WHERE media_token='expired-token'").fetchone(); conn.close()
    assert stories == [] and row is None


def test_post_edit_like_and_comment_routes():
    conn=app.get_db_connection(); uid=create_user(conn,"Alice","alice",1); conn.commit(); conn.close()
    client=app.app.test_client(); login_user(client,uid)
    assert client.post('/posts/create',data={'caption':'Original','post_type':'post','csrf_token':'test-csrf'},follow_redirects=False).status_code==302
    conn=app.get_db_connection(); post=conn.execute('SELECT id FROM posts WHERE user_id=?',(uid,)).fetchone(); conn.close()
    pid=post['id']
    assert client.post(f'/post/{pid}/edit',data={'caption':'Edited','csrf_token':'test-csrf'}).status_code==200
    assert client.post(f'/post/{pid}/like',headers={'X-CSRF-Token':'test-csrf'}).status_code==200
    assert client.post(f'/post/{pid}/comment',data={'comment':'Nice post','csrf_token':'test-csrf'}).status_code==200


def test_open_chat_group_create_and_join():
    conn=app.get_db_connection(); a=create_user(conn,"Alice","alice",1); b=create_user(conn,"Bob","bob",1); conn.commit(); conn.close()
    client=app.app.test_client(); login_user(client,a)
    r=client.post('/chat-groups/create',data={'name':'Study Chat','description':'Study together','kind':'open','csrf_token':'test-csrf'},follow_redirects=False)
    assert r.status_code==302
    conn=app.get_db_connection(); gid=conn.execute('SELECT id FROM chat_groups WHERE name=?',('Study Chat',)).fetchone()['id']; conn.close()
    login_user(client,b); assert client.post(f'/chat-groups/{gid}/join',headers={'X-CSRF-Token':'test-csrf'}).status_code==200
    r=client.post(f'/chat-groups/{gid}/send',data={'message':'Hello group','csrf_token':'test-csrf'},headers={'X-Requested-With':'XMLHttpRequest'}); assert r.status_code==200
