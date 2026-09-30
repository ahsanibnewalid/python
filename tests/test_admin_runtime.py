from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_render_uses_persistent_admin_runtime():
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "startCommand: python production_admin_runtime.py" in render
    assert "ADMIN_BOOTSTRAP_TOKEN" in render
    assert "DATABASE_URL" in render


def test_admin_runtime_has_one_time_bootstrap_and_password_change():
    source = (ROOT / "admin_runtime.py").read_text(encoding="utf-8")
    assert "/admin/first-setup" in source
    assert "/admin/password" in source
    assert "ADMIN_BOOTSTRAP_TOKEN" in source
    assert "setup_completed" in source
    assert "generate_password_hash" in source
    assert "check_password_hash" in source


def test_runtime_wrapper_persists_credentials_before_app_import():
    source = (ROOT / "production_admin_runtime.py").read_text(encoding="utf-8")
    assert "admin_credentials" in source
    assert "ADMIN_PASSWORD_HASH" in source
    assert "gunicorn_admin.conf.py" in source
