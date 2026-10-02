import os
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

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
os.environ["MEDIA_STORAGE"] = "local"
os.environ["MAX_UPLOAD_MB"] = "512"

import app

app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)


def reset_db():
    conn = app.get_db_connection()
    # Preserve schema, remove test data in dependency-safe order.
    for table in ["chat_group_messages", "chat_group_members", "chat_groups", "post_comments", "post_likes", "posts", "stories", "messages", "user_devices", "user_keys", "user_blocks", "chat_preferences", "mobile_refresh_tokens", "users"]:
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


def test_plaintext_message_is_allowed():
    conn = app.get_db_connection(); a = create_user(conn, "Alice", "alice", 1); b = create_user(conn, "Bob", "bob", 1); conn.commit(); conn.close()
    client = app.app.test_client(); login_user(client, a)
    r = client.post("/messages/send", data={"receiver_id": str(b), "message":"plaintext", "csrf_token":"test-csrf"}, headers={"X-Requested-With":"XMLHttpRequest"})
    assert r.status_code == 200
    conn = app.get_db_connection(); row = conn.execute("SELECT message,encryption_version FROM messages WHERE sender_id=?", (a,)).fetchone(); conn.close()
    assert row["message"] == "plaintext" and row["encryption_version"] == 0




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


def test_admin_login_recovers_from_stale_csrf_session():
    client = app.app.test_client()

    page = client.get("/login")
    assert page.status_code == 200

    with client.session_transaction() as sess:
        sess["csrf_token"] = "stale-token"

    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "test-password",
            "csrf_token": "old-token",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")

    fresh = client.get("/login")
    assert fresh.status_code == 200

    import re
    match = re.search(rb'name="csrf_token" value="([^"]+)"', fresh.data)
    assert match

    login = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "test-password",
            "csrf_token": match.group(1).decode(),
        },
        follow_redirects=False,
    )
    assert login.status_code == 302
    assert login.headers["Location"].endswith("/admin/god")


def test_message_notifications_are_incremental_after_cursor():
    conn=app.get_db_connection()
    sender=create_user(conn,"Alice","alice",1)
    receiver=create_user(conn,"Bob","bob",1)
    conn.execute(
        "INSERT INTO messages(sender_id,receiver_id,message,created_at,is_read) VALUES(?,?,?,?,0)",
        (sender,receiver,"first","2026-09-30 03:00:00")
    )
    conn.commit()
    first_id=conn.execute("SELECT id FROM messages ORDER BY id DESC LIMIT 1").fetchone()["id"]
    conn.close()

    client=app.app.test_client()
    login_user(client,receiver)

    initial=client.get("/api/message-notifications?after=0")
    assert initial.status_code == 200
    assert initial.get_json()["messages"]

    conn=app.get_db_connection()
    conn.execute(
        "INSERT INTO messages(sender_id,receiver_id,message,created_at,is_read) VALUES(?,?,?,?,0)",
        (sender,receiver,"second","2026-09-30 03:01:00")
    )
    conn.commit()
    second_id=conn.execute("SELECT id FROM messages ORDER BY id DESC LIMIT 1").fetchone()["id"]
    conn.close()

    incremental=client.get(f"/api/message-notifications?after={first_id}")
    data=incremental.get_json()
    assert incremental.status_code == 200
    assert [m["id"] for m in data["messages"]] == [second_id]
    assert data["latest_id"] == second_id


def test_user_home_is_not_cached():
    conn=app.get_db_connection()
    uid=create_user(conn,"Alice","alice",1)
    conn.commit()
    conn.close()
    client=app.app.test_client()
    login_user(client,uid)
    response=client.get("/user-home")
    assert response.status_code == 200
    assert "no-store" in response.headers.get("Cache-Control","").lower()


def test_admin_profile_template_has_valid_gallery_csrf_markup():
    template=(TEST_ROOT / "templates" / "profile.html").read_text(encoding="utf-8")
    assert '<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">' in template
    assert '<input type="hidden" name="form_type" value="gallery_upload">' in template
    assert '<input\n                    type="hidden"\n                <input' not in template


def _jpeg_bytes():
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00" + b"\x08" * 64 +
        b"\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x01\x11\x00" +
        b"\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00" +
        b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x00\xff\xd9"
    )


def _mp4_ftyp_bytes():
    return b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"


def _private_path(absolute_url):
    parsed = urlsplit(absolute_url)
    return parsed.path + (("?" + parsed.query) if parsed.query else "")


def test_web_post_reel_and_story_multipart_media_round_trip():
    conn = app.get_db_connection()
    uid = create_user(conn, "Alice", "alice", 1)
    conn.commit()
    conn.close()
    client = app.app.test_client()
    login_user(client, uid)

    photo = _jpeg_bytes()
    post = client.post(
        "/posts/create",
        data={
            "caption": "Photo upload",
            "post_type": "post",
            "csrf_token": "test-csrf",
            "media": (BytesIO(photo), "photo.jpg", "image/jpeg"),
        },
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
    )
    assert post.status_code == 200, post.get_data(as_text=True)
    conn = app.get_db_connection()
    row = conn.execute("SELECT * FROM posts WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
    conn.close()
    assert row["post_type"] == "post"
    assert row["media_type"] == "image"
    assert row["original_name"] == "photo.jpg"
    media_response = client.get(
        f"/private-post-media/{row['media_token']}",
        follow_redirects=False,
    )
    assert media_response.status_code == 200
    assert media_response.data == photo

    reel = client.post(
        "/posts/create",
        data={
            "caption": "Video upload",
            "post_type": "reel",
            "csrf_token": "test-csrf",
            "media": (BytesIO(_mp4_ftyp_bytes()), "clip.mp4", "video/mp4"),
        },
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
    )
    assert reel.status_code == 200, reel.get_data(as_text=True)
    conn = app.get_db_connection()
    reel_row = conn.execute(
        "SELECT * FROM posts WHERE user_id=? AND post_type='reel' ORDER BY id DESC LIMIT 1",
        (uid,),
    ).fetchone()
    conn.close()
    assert reel_row["media_type"] == "video"

    story_photo = _jpeg_bytes()
    story = client.post(
        "/stories/create",
        data={
            "story_caption": "Story upload",
            "csrf_token": "test-csrf",
            "story_media": (BytesIO(story_photo), "story.jpg", "image/jpeg"),
        },
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
    )
    assert story.status_code == 200, story.get_data(as_text=True)
    conn = app.get_db_connection()
    story_row = conn.execute(
        "SELECT * FROM stories WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (uid,),
    ).fetchone()
    conn.close()
    assert story_row["media_type"] == "image"
    story_media = client.get(f"/private-story-media/{story_row['media_token']}")
    assert story_media.status_code == 200
    assert story_media.data == story_photo


def test_web_media_rejects_mismatched_signature():
    conn = app.get_db_connection()
    uid = create_user(conn, "Alice", "alice", 1)
    conn.commit()
    conn.close()
    client = app.app.test_client()
    login_user(client, uid)
    response = client.post(
        "/posts/create",
        data={
            "caption": "bad media",
            "post_type": "post",
            "csrf_token": "test-csrf",
            "media": (BytesIO(b"not a jpeg"), "fake.jpg", "image/jpeg"),
        },
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
    )
    assert response.status_code in {400, 415}
    assert "media" in response.get_json()["error"].lower()


def test_mobile_api_login_refresh_me_and_media_upload():
    conn = app.get_db_connection()
    uid = create_user(conn, "Alice", "alice", 1)
    conn.commit()
    conn.close()
    client = app.app.test_client()

    login = client.post(
        "/api/v1/auth/login",
        json={"identifier": "alice", "password": "password"},
    )
    assert login.status_code == 200, login.get_data(as_text=True)
    payload = login.get_json()
    access = payload["access_token"]
    refresh = payload["refresh_token"]
    assert payload["token_type"] == "Bearer"

    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.get_json()["user"]["id"] == uid

    post = client.post(
        "/api/v1/posts",
        data={
            "caption": "Native upload",
            "post_type": "post",
            "media": (BytesIO(_jpeg_bytes()), "native.jpg", "image/jpeg"),
        },
        headers={"Authorization": f"Bearer {access}"},
        content_type="multipart/form-data",
    )
    assert post.status_code == 201, post.get_data(as_text=True)
    post_data = post.get_json()
    assert post_data["post_id"] > 0
    assert post_data["media_url"]

    media = client.get(_private_path(post_data["media_url"]))
    assert media.status_code == 200

    refresh_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh},
    )
    assert refresh_response.status_code == 200
    rotated = refresh_response.get_json()
    assert rotated["access_token"] != access
    assert rotated["refresh_token"] != refresh

    old_refresh = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh},
    )
    assert old_refresh.status_code == 401


def test_mobile_api_registration_and_core_feed_routes():
    client = app.app.test_client()
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "name": "Mobile User",
            "username": "mobileuser",
            "gmail": "mobileuser@gmail.com",
            "phone": "+8801712345678",
            "password": "password",
        },
    )
    assert registration.status_code == 201, registration.get_data(as_text=True)
    payload = registration.get_json()
    access = payload["access_token"]
    assert payload["user"]["username"] == "mobileuser"

    feed = client.get("/api/v1/feed", headers={"Authorization": f"Bearer {access}"})
    assert feed.status_code == 200
    assert "posts" in feed.get_json()

    conversations = client.get(
        "/api/v1/conversations",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert conversations.status_code == 200
    assert conversations.get_json()["conversations"] == []


def test_mobile_media_urls_are_short_lived_and_do_not_require_browser_session():
    conn = app.get_db_connection()
    uid = create_user(conn, "Alice", "alice", 1)
    conn.commit()
    conn.close()
    client = app.app.test_client()
    login = client.post(
        "/api/v1/auth/login",
        json={"identifier": "alice", "password": "password"},
    )
    access = login.get_json()["access_token"]
    created = client.post(
        "/api/v1/stories",
        data={"caption": "native story", "media": (BytesIO(_jpeg_bytes()), "story.jpg", "image/jpeg")},
        headers={"Authorization": f"Bearer {access}"},
        content_type="multipart/form-data",
    )
    assert created.status_code == 201
    url = created.get_json()["media_url"]
    media = client.get(_private_path(url))
    assert media.status_code == 200
    assert media.data == _jpeg_bytes()
