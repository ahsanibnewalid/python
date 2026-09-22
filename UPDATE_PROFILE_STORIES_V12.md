# University Connect — Profile / Stories / Feed Fixes (V12)

## Fixed

1. **Profile Recent Posts**
   - Profile pages now query only posts authored by that profile owner.
   - The community feed query is no longer reused for profile pages.

2. **Facebook-style profile tabs**
   - About, Posts, Photos and Professional are now real interactive tabs.
   - Clicking a tab switches the visible section without leaving the profile.
   - The selected tab is preserved in the URL hash (`#about`, `#posts`, `#photos`, `#professional`).
   - About shows intro/basic/education/skills.
   - Posts shows only that user's posts/reels.
   - Photos shows that user's gallery.
   - Professional shows career, education, projects, certifications and related information.

3. **Stories UI / upload experience**
   - Story cards now show the actual uploaded story media instead of always using the user's profile photo as the story image.
   - Video stories use a muted video thumbnail.
   - Added client-side file-size validation (60 MB) and a preview before upload.
   - Existing 24-hour expiration remains in place.

4. **Premium CV link**
   - `/my-profile?view=cv` now actually opens the premium CV layout instead of silently ignoring the query parameter.

5. **SQLite messaging stability**
   - Added a 10-second SQLite busy timeout and WAL mode to reduce `database is locked` errors during polling.
   - E2EE device registration now updates an existing device before attempting an insert, with a race-safe retry.

## Log evidence addressed

The supplied server log showed:
- `UNIQUE constraint failed: user_devices.device_id`
- repeated `sqlite3.OperationalError: database is locked`

Those two backend issues are addressed in `app.py`.
