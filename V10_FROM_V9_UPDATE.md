# University Connect — V9 baseline update

This build is based directly on UniversityConnect_V9_Stories_Fix.

Changes:
- Preserves the V9 working Flask startup and messaging implementation.
- Text-only newsfeed posts are supported without requiring media.
- Photo and video posts remain supported; video can be published as a Reel from the composer.
- Story media cards use the uploaded story media, with image/video viewing.
- Added user cover-photo field and upload.
- Added public profile format choice: Facebook Detailed Profile or Premium Professional CV.
- Changing profile photo or cover photo automatically creates a newsfeed post.
- Added a Facebook-style public profile template.
- Existing detailed CV template is retained and branded as Premium Professional CV.
- Database changes are additive migrations; keep the existing database.db and media folders.
