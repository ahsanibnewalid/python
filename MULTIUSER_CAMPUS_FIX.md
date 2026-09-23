# Multi-user / Admin / Campus fixes

- Admin profile editor now accepts CSRF tokens and can update the public profile fields, study/CV fields, and profile format.
- Admin can update a user's profile and cover photos from the admin profile editor.
- Linked chat groups are discoverable by members of their source UniversityConnect group.
- New source-group members are added to the linked chat lazily when they open/list the chat.
- Linked chat join is restricted to members of the source group.
- Campus-created groups now get a real member administrator (selected by admin or the first member of the selected university), instead of a group with `created_by=NULL` and no usable admin.
