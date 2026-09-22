# University Connect — Combined Build

This package combines the uploaded **UniversityConnect_V6_Fixed** build with the navigation, session-isolation, mobile, CV/profile, newsfeed, messaging, security and private-media work from the previous University Connect build.

## Preserved from V6
- Campus Services: universities, departments, batches, announcements, clubs, events, notifications
- Groups: creation, privacy, membership, admins, join requests, group posts, likes/comments and polling updates
- Admin campus management
- Admin analytics and database backup
- Admin user management and organization/role assignment
- Existing newsfeed/reels/photos, likes and comments
- Existing messaging and API endpoints
- Existing profile/CV and study information
- Existing CSRF, upload validation, security headers and HTTPS configuration

## Combined fixes
- Admin and student sessions no longer clear each other when logging in/out in the same browser.
- Successful sessions are permanent for 7 days and refresh while active.
- Safe same-site return/back navigation is available on authenticated secondary pages.
- Mobile bottom navigation is available throughout the student area, including Home, Feed, Messages, Profile and More.
- Mobile More menu exposes Campus, Groups, Students, Profile/Study Settings, CV Profile and Logout.
- Admin mobile navigation exposes Dashboard, Activity, Campus, Users and More.
- Added the missing responsive Activity Log template.
- Mobile navigation is also available on Campus, Groups and group-detail pages.
- Existing V6 functionality was kept instead of replacing it with the older GitHub/main version.

## Deployment
Recommended Render settings:
- Build: `pip install -r requirements-production.txt`
- Start: `gunicorn app:app`
- Set `APP_ENV=production`
- Set a strong `FLASK_SECRET_KEY`
- Set `ADMIN_PASSWORD_HASH`
- For Render proxy/HTTPS deployment, set `TRUST_PROXY=1`, `REQUIRE_HTTPS=1`, and `COOKIE_SECURE=1`

The application still uses SQLite/local media in this build. For production scale, use persistent storage or migrate to PostgreSQL/object storage as described in `IMPLEMENTED.md` and `SECURITY_NOTES.txt`.

## Validation performed
- `app.py` passed Python compilation.
- All 22 HTML templates parsed successfully with Jinja2.
- All `url_for()` references found in templates map to existing Flask endpoints.
- Full pytest execution could not run in the build environment because the environment does not have the project's Flask/Werkzeug dependencies installed; the package's requirements files are included for installation.


## Messenger optimization update
- Voice calling and video calling/WebRTC functionality has been removed from the chat UI and backend to reduce browser/device overhead.
- Private messaging, E2EE key handling, chat themes/wallpapers, disappearing messages, safety number, encrypted backup/restore, block/unblock, reporting, and profile access remain.
