# Render production setup

## Why accounts were disappearing

University Connect supports SQLite for local development, but Render web-service filesystems are ephemeral. A local `database.db` is therefore not a production datastore.

The repository now includes `render.yaml` with a Render Postgres database connected through `DATABASE_URL`. Deploying the blueprint makes the application use PostgreSQL automatically.

Render currently offers a Free Postgres instance for testing, but documents that Free Postgres expires after 30 days and has no backups. Move to a paid database before relying on it for real users.

## First deployment

1. In Render, choose **New > Blueprint** and select this repository.
2. Review `render.yaml`.
3. Create the services.
4. Set `ADMIN_USER=admin` (or your preferred owner username) and `ADMIN_PASSWORD` in the web service environment for the first deployment. The production launcher hashes `ADMIN_PASSWORD` into PostgreSQL; do not store a real password in Git.
5. Deploy.
6. Confirm the web service has a `DATABASE_URL` supplied from `university-connect-db`.
7. Register a test account.
8. Sign in to `/login`; the System Owner console is `/admin/god`.
9. Let the free web service spin down or redeploy it.
10. Log in again. The account should still exist because it is stored in Postgres, not the web-service filesystem.

## Media

The application contains an S3-compatible media adapter in `services/media_storage.py`. For production media, configure `MEDIA_STORAGE=s3` and supply an S3/R2/B2-compatible bucket and credentials.

Do not put media on the Render web-service filesystem. Free Render web services lose filesystem changes when they restart, redeploy, or spin down.

For a temporary single-instance paid Render deployment, a persistent disk can preserve local media, but Render documents that a disk is attached to only one service instance and prevents horizontal scaling. Object storage is therefore the intended long-term architecture for a social product.

## Billing

Billing remains optional. The default blueprint uses `BILLING_ENABLED=false` and `BILLING_PROVIDER=none`. A website owner can enable a supported provider later without making payment credentials a requirement for normal deployments.


## Production hardening notes

Keep MEDIA_STORAGE=s3 in production and configure the S3-compatible bucket credentials. The web-service filesystem is not a durable social-media store.

CI executes the real pytest suite with python -m pytest -q so the project's pytest-style tests are collected and executed.

The application health endpoint /healthz checks the database and required media-storage credentials and is now used by Render health checks.

Rotate any credential that may have appeared in historical Git commits; removing a secret from the latest tree does not invalidate an exposed historical value.
