from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_system_owner_moderator_lifecycle_is_complete():
    source = (ROOT / "admin_god.py").read_text(encoding="utf-8")
    for marker in (
        '/admin/god/staff/add',
        '/admin/god/staff/<int:staff_id>/scopes',
        '/admin/god/staff/<int:staff_id>/toggle',
        '/admin/god/staff/<int:staff_id>/remove',
        'moderator_removed',
        'platform_notifications',
    ):
        assert marker in source


def test_social_notification_center_is_canonical():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "notifications.html").read_text(encoding="utf-8")
    assert '@app.route("/notifications")' in app
    assert 'FROM platform_notifications' in app
    assert '/api/notifications' in template
    assert '/platform/notifications/' in template


def test_social_home_media_and_notification_ui_guards():
    home = (ROOT / "templates" / "user_home.html").read_text(encoding="utf-8")
    assert home.count("<style>") == 1
    assert home.count("</style>") == 1
    assert 'id="notificationToggle"' in home
    assert "loadNotifications(false)" in home
    assert "document.addEventListener('play'" in home


def test_production_media_backend_has_object_store_fallbacks():
    storage = (ROOT / "services" / "media_storage.py").read_text(encoding="utf-8")
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "presigned_get_url" in storage
    assert "save_private_media" in app
    assert "save_public_upload" in app
    assert 'MEDIA_STORAGE' in app


def test_high_volume_indexes_use_existing_columns():
    v2 = (ROOT / "v2_core.py").read_text(encoding="utf-8")
    assert "idx_job_applications_applicant_status" in v2
    assert "idx_job_applications_user_status" not in v2
    for name in (
        "idx_institution_memberships_user_status",
        "idx_course_enrollments_user_status",
        "idx_platform_notifications_user_read",
        "idx_conversation_messages_conversation_created",
    ):
        assert name in v2


def test_admin_runtime_does_not_query_credentials_on_user_pages():
    source = (ROOT / "admin_runtime.py").read_text(encoding="utf-8")
    assert 'request.path == "/login" or request.path.startswith("/admin")' in source


def test_bulk_notification_read_and_group_comment_errors_are_hardened():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '@app.route("/notifications/read-all", methods=["POST"])' in app
    assert 'Post not found.' in app
    assert 'group_id=post["group_id"] if post else 1' not in app


def test_failed_upload_cleanup_hooks_exist():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    v2 = (ROOT / "v2_core.py").read_text(encoding="utf-8")
    assert 'remove_study_resource_file(file_path)' in v2
    assert 'get_media_storage().delete(key)' in app
