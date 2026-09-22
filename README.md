# University Connect

A Flask-based university community platform with student profiles, social feed, private messaging and campus management.

## Current commercial MVP

### Basic
- Student directory
- Profiles / CV information
- Private messaging
- Announcements
- Admin dashboard

### Professional MVP
- Posts, likes and comments
- Universities
- Departments
- Batches
- Groups and group membership
- Clubs
- Events and event registration
- Notifications
- Platform analytics
- Branding configuration

### Enterprise foundation
- Multiple administrators via roles
- Database backup download
- Custom-domain configuration storage
- Role framework for university/depart­ment/moderator/teacher/student/alumni

## Important production gaps

This is a commercial MVP foundation, not yet a finished SaaS billing platform. Payment processing, automated custom-domain DNS/SSL, strict per-tenant authorization across every route, object storage, WebSockets and production observability still need implementation before selling to institutions at scale. PostgreSQL persistence is now supported through `DATABASE_URL`; local development can continue using SQLite.

## Run locally

Use the supplied installation/start scripts and configure `.env` from `.env.example`.

## Group / Community Features (V5)

Groups are now user-created communities, not admin-only objects. A logged-in user linked to a university can create a group and becomes its owner and first group admin.

Privacy modes:
- Public — any logged-in user can join.
- University — members must belong to the group's university.
- Department — members must belong to the selected department.
- Private — users submit join requests; group admins approve or reject them.

Group features:
- Create and join/leave groups
- Group owner and additional group admins
- Group feed with text posts
- Likes and comments
- Member list
- Remove members
- Promote/demote group admins
- Private-group join requests with approval/rejection
- University and department visibility rules

## Admin Dashboard Login

The public domain opens the user portal first. The admin dashboard is protected and is not linked from the public user login page.

1. Open: `/login`
2. Username: the value of `ADMIN_USER` (default local username: `admin`)
3. Password: the admin password configured by `setup_local.py` or `ADMIN_PASSWORD_HASH` in `.env`
4. After successful login you are redirected to `/admin`.

For a fresh local installation, run `python setup_local.py` once and choose the admin password. Do not use the development fallback password in a real deployment.

## Production database (Render)

The app uses SQLite when `DATABASE_URL` is empty and PostgreSQL when `DATABASE_URL` is set. For Render, create a PostgreSQL database and add its **Internal Database URL** to the web service as `DATABASE_URL`. Do not commit `database.db` or database credentials to GitHub.

The chat system stores `is_read`, `delivered_at` and `read_at` so the sender can see `✓`, `✓✓` and `✓✓ Seen` while the chat page polls for updates.

For a live Render deployment, the database is the persistent source of truth; the web service filesystem should not be used as the permanent database.


## Messenger optimization update
- Voice calling and video calling/WebRTC functionality has been removed from the chat UI and backend to reduce browser/device overhead.
- Private messaging, E2EE key handling, chat themes/wallpapers, disappearing messages, safety number, encrypted backup/restore, block/unblock, reporting, and profile access remain.
