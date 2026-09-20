import os, secrets, getpass
from pathlib import Path
from werkzeug.security import generate_password_hash

root = Path(__file__).resolve().parent
env_file = root / ".env"

if env_file.exists():
    print(".env already exists; leaving it unchanged.")
    raise SystemExit(0)

password = getpass.getpass("Choose a local admin password: ")
if not password:
    raise SystemExit("Password cannot be empty.")
confirm = getpass.getpass("Confirm admin password: ")
if password != confirm:
    raise SystemExit("Passwords do not match.")

secret = secrets.token_urlsafe(48)
env = f"""APP_ENV=development
FLASK_SECRET_KEY={secret}
ADMIN_USER=admin
ADMIN_PASSWORD_HASH={generate_password_hash(password)}
SITE_NAME=University Connect
COOKIE_SECURE=0
REQUIRE_HTTPS=0
TRUST_PROXY=0
"""
env_file.write_text(env, encoding="utf-8")
print("Created .env with a random Flask secret and hashed admin password.")
