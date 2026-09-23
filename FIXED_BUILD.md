# UniversityConnect — Phase 1–5 + Social/Chat Repair Build

This build contains the security, database, media, authentication, social-feed, profile, messaging, and chat-group repairs applied to the uploaded project.

## Major repairs

- Secrets and live database data are excluded from the source archive.
- `DB_FILE` is configurable through the environment.
- Test database isolation is configured in `tests/test_app.py`.
- Broken helper scripts were repaired.
- Post/Reel creation now rolls back saved media when the database write fails.
- Story upload uses one unified composer and validates the uploaded media signature.
- Stories expire and their database rows/media are cleaned up.
- Feed and people discovery are university-scoped where a university is assigned.
- Private post/story media is authenticated and university-scoped.
- Users can edit and delete their own posts from the feed and their own Facebook-style profile.
- Like/react and comment actions are wired to the same backend endpoints from the feed and profile.
- Chat read-state logic respects blocking and expired messages.
- E2EE is now optional per one-to-one conversation. Normal messages are allowed when E2EE is off; encrypted messages are used when the user explicitly enables E2EE in Chat settings.
- E2EE key derivation uses a symmetric public-key-derived salt so sender and recipient derive the same AES key.
- The three-dot chat menu no longer controls E2EE.
- Added open chat groups that users can create and join.
- Added linked chat groups that copy the current members of an existing UniversityConnect social group.
- Added chat-group messaging, membership, join/leave behavior, and polling.
- Added chat-group navigation from Messages and existing social-group pages.
- User media is cleaned when a user is deleted.
- Profile/cover automatic feed-post creation is transactional with the profile update.
- Expanded automated tests for optional E2EE, post editing/reactions/comments, device ownership, story cleanup, and chat groups.

## Security configuration

Create environment variables before production deployment:

- `APP_ENV=production`
- `FLASK_SECRET_KEY=<new random secret>`
- `ADMIN_USER=<admin username>`
- `ADMIN_PASSWORD_HASH=<Werkzeug password hash>`
- `DB_FILE=<persistent database path or PostgreSQL configuration used by your deployment>`
- `COOKIE_SECURE=1` when served only over HTTPS
- `REQUIRE_HTTPS=1` when appropriate

Never restore an old committed `.env` or `database.db`.

## Verification performed in this environment

- All Python files pass `py_compile`.
- All Jinja templates pass Jinja syntax compilation.
- All referenced `url_for()` endpoint names resolve to application route functions by static inspection.
- JavaScript blocks in the repaired social/chat templates pass Node syntax checking after template-token stripping.

The execution environment used for this repair did not have Flask/Werkzeug installed and could not download packages, so a live Flask/pytest integration run could not be performed here. Run `pytest -q` inside the project's normal virtual environment before deployment.
