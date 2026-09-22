# Social / Profile Update

This build addresses the requested social-media and profile improvements:

- Bangladesh phone registration accepts local `01XXXXXXXXX` numbers and normalizes them to `+8801XXXXXXXXX`.
- Registration defaults to Bangladesh while still allowing the country selector to be changed.
- Posts support text-only publishing, photo posts, and video posts.
- Reels are stored as a distinct `post_type=reel` and require video media.
- Stories support image and video uploads with 24-hour expiry and an in-app viewer.
- Profile photo changes automatically create a historical feed post.
- Cover photo upload is available from the Facebook-style profile and automatically creates a historical feed post.
- Public profiles can be switched between:
  - Facebook Detailed Profile
  - Premium Professional CV
- The CV format was upgraded for Bangladesh job applications with career objective, professional summary, experience, projects, education, certifications/training, achievements, skills, academic profile, personal details and references, plus A4 print/PDF support.
- Added database migrations for `posts.post_type` and the new CV fields, so existing SQLite databases are upgraded automatically.
