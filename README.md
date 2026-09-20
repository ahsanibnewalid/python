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

This is a commercial MVP foundation, not yet a finished SaaS billing platform. Payment processing, automated custom-domain DNS/SSL, strict per-tenant authorization across every route, PostgreSQL, object storage, WebSockets and production observability still need implementation before selling to institutions at scale.

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
