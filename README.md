# University Connect

University Connect is a Flask-based multi-tenant community and management platform with a web application, versioned native API, and an Expo/React Native Android client. The repository contains social networking, messaging, academic/institution management, organization/workplace features, recruitment, study resources, trust/verification workflows, administration, and an admin-controlled Android APK build pipeline.

## Platform capabilities

### Social and community
- User accounts, profiles and CV information
- Posts, likes, comments, stories and reels/media
- User search and notifications
- Private messaging and conversations
- Read/delivery states for chat messages
- Groups with public, university, department and private privacy modes
- Group owners/admins, membership approval, posts, likes and comments
- Clubs, events and registrations
- Blocking/reporting and messaging safety controls
- E2EE device/key handling, safety-number support and encrypted backup/restore

### Institutions, campus and academics
- Universities/institutions and public institution pages
- Institution memberships and role delegation
- Departments, programs, academic sessions and courses
- Course enrollment and academic notices
- Study resources, enrollment and starring
- Recorded classes/resources
- Student/staff/teacher/session-coordinator style role framework
- Organization and institution trust/verification workflow
- Private identity/role proof for membership approval

### Organizations, office and careers
- Organizations and public organization pages
- Organization memberships and delegated roles
- Office/HR/team workflows and duties
- Jobs, applications, recruitment and application events
- CV profiles and documents
- Public organization updates/notices

### Administration and SaaS foundation
- System Owner command center at `/admin/god`
- Owner-controlled moderators with scoped responsibilities
- Moderation work assignment and audit trail
- Platform trust/verification queue
- PostgreSQL support for persistent production deployments
- SQLite support for local development
- S3-compatible object storage support (including S3/R2/B2 configuration)
- Optional billing configuration; payment is not forced on every deployment
- Versioned native API with bearer access tokens and rotating refresh tokens
- Production security settings for HTTPS, secure cookies, proxy handling and secret configuration

## Android mobile application

The `mobile/` directory is an Expo Router / React Native TypeScript application. It uses Expo SDK 57, React Native 0.86, SecureStore for native token storage, media pickers/video support, and the native API.

Current mobile areas include:
- Authentication and registration
- Feed, posts and media
- Stories/reels
- Search
- Notifications
- Messaging and conversations
- Profile
- Workspace/platform areas

Run the mobile project locally:

```bash
cd mobile
npm install
npm run typecheck
npx expo start
```

## Automated Android APK

The repository includes `.github/workflows/build-android-apk.yml`.

The workflow:
1. Checks out `main`.
2. Installs Node 22.13.1 and Java 17.
3. Installs Android API 36 / Build Tools 36.0.0.
4. Installs the mobile dependencies.
5. Runs Expo prebuild for Android.
6. Runs Gradle `assembleDebug`.
7. Uploads `university-connect-android-debug` containing `app-debug.apk`.

It can run automatically when `mobile/**` changes on `main`, or manually with GitHub Actions `workflow_dispatch`.

### Build an APK from the System Owner panel

The System Owner can build an installable debug APK without manually opening GitHub Actions:

1. Configure the three Render environment variables below.
2. Sign in as the System Owner.
3. Open `/admin/god`.
4. In **Android APK Builder**, click **Test GitHub connection**.
5. If the test reports Connected, click **Build installable APK**.
6. The panel polls the GitHub Actions run and exposes **Open build** and **Download APK** when the artifact is ready.

The server never displays the GitHub token. It uses the token only for GitHub API calls.

Required Render variables:

```text
GITHUB_ACTIONS_TOKEN=<GitHub fine-grained token>
GITHUB_ACTIONS_REPO=ahsanibnewalid/python
GITHUB_APK_WORKFLOW=build-android-apk.yml
```

`GITHUB_ACTIONS_REPO` and `GITHUB_APK_WORKFLOW` have defaults, so only `GITHUB_ACTIONS_TOKEN` is strictly required for the standard repository/workflow.

For a fine-grained GitHub token, grant access to this repository and the minimum repository permissions needed to read the repository/workflow and dispatch the workflow. Do not commit the token or place it in source code.

## Production deployment on Render

The supplied `render.yaml` defines:
- A Python web service
- PostgreSQL persistence
- Generated `FLASK_SECRET_KEY`
- HTTPS/secure-cookie/proxy settings
- Optional S3-compatible media storage
- Billing disabled by default

### Required first-deployment configuration

Set a strong private value for `ADMIN_PASSWORD` before the first production start. Also configure `FLASK_SECRET_KEY` (Render can generate it), `DATABASE_URL`, and your media-storage credentials.

For persistent production media, use `MEDIA_STORAGE=s3` (or another supported S3-compatible provider) rather than local filesystem storage.

Example environment baseline:

```text
APP_ENV=production
ADMIN_USER=admin
ADMIN_PASSWORD=<strong-private-password>
FLASK_SECRET_KEY=<long-random-secret>
DATABASE_URL=<Render PostgreSQL Internal Database URL>
TRUST_PROXY=1
REQUIRE_HTTPS=1
COOKIE_SECURE=1
BILLING_ENABLED=false
BILLING_PROVIDER=none
MEDIA_STORAGE=s3
MEDIA_S3_BUCKET=<bucket>
MEDIA_S3_ENDPOINT=<endpoint>
MEDIA_S3_REGION=auto
MEDIA_S3_ACCESS_KEY=<access-key>
MEDIA_S3_SECRET_KEY=<secret-key>
MEDIA_S3_PREFIX=university-connect
```

Do not commit `.env`, real credentials, database URLs, API tokens, or generated secrets.

## Local setup

Use the repository bootstrap script and a private `.env` file:

```bash
python setup_local.py
python production_admin_runtime.py
```

Or use the normal Flask development entry point used by the repository when developing locally.

Health check:

```text
GET /healthz
```

## Database

- SQLite is supported for local development when `DATABASE_URL` is empty.
- PostgreSQL is the intended persistent production database.
- Render deployments should use the PostgreSQL Internal Database URL.
- The web-service filesystem should not be treated as permanent database storage.

## Media uploads

The application supports local and S3-compatible object storage. Browser uploads are designed to accept legitimate image/video media, and `MAX_UPLOAD_MB` can be configured for larger media.

For high-volume production video, direct/resumable object-storage uploads are preferable to routing very large files through the Flask process.

## Native API

`api_v1.py` provides the versioned native API used by the mobile client. It includes native authentication with bearer access tokens and rotating refresh tokens, plus feed, media, stories, posts, messaging, profile, search, notifications and workspace-related endpoints.

Keep the API and web session authorization boundaries intact when adding new mobile features.

## Trust and verification

Organizations and institutions can register public platform spaces. The trust model separates:
- **Platform verification:** System Owner reviews organization/institution evidence and verifies the public page.
- **Membership verification:** The organization/institution administrator reviews a person's identity/role proof and approves the membership or role.

Proof documents are intended to remain private; public pages expose the verification state and approved public information rather than private evidence.

## Billing

Billing is optional. Deployments may leave billing disabled or configure the supported provider settings according to the deployment's commercial model. Core platform access does not require a payment integration to be enabled.

## Security and operational notes

- Use HTTPS in production.
- Keep `FLASK_SECRET_KEY`, admin credentials, database credentials, object-storage credentials and GitHub tokens private.
- Use the System Owner console for privileged administrative operations.
- Review organization/institution proof before platform verification.
- Rotate any credential that has ever been committed to Git history.
- Production-scale deployments should add centralized logs, monitoring, backups and tenant-specific authorization hardening before broad institutional rollout.

## Repository structure

```text
.
├── app.py
├── api_v1.py
├── v2_core.py
├── admin_god.py
├── templates/
├── static/
├── services/
├── tests/
├── mobile/
│   ├── app/
│   ├── src/
│   ├── package.json
│   └── eas.json
├── .github/workflows/
│   └── build-android-apk.yml
├── render.yaml
├── requirements.txt
└── .env.example
```

## Current project status

The repository has automated backend/mobile CI and an automated Android debug APK workflow. The Android artifact is an installable debug APK intended for testing/distribution outside the Play Store; a production Play Store release should use a properly signed release build and the required Android/Google Play credentials.

Before a large commercial rollout, continue hardening tenant isolation/authorization, production observability, backups/disaster recovery, object-storage delivery, rate limiting and other operational controls appropriate to the deployment size.
