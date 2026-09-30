# Production media storage on Render

University Connect supports two production-safe storage strategies.

## Recommended: S3-compatible object storage

Set these Render environment variables:

```text
MEDIA_STORAGE=s3
MEDIA_S3_BUCKET=your-bucket
MEDIA_S3_ENDPOINT=https://<provider-endpoint>
MEDIA_S3_REGION=auto
MEDIA_S3_ACCESS_KEY=...
MEDIA_S3_SECRET_KEY=...
MEDIA_S3_PREFIX=university-connect
```

The endpoint can be AWS S3, Cloudflare R2, Backblaze B2's S3 API, MinIO, or another S3-compatible provider.

Do not commit credentials to GitHub.

## Simple Render option: persistent disk

For a single-instance deployment, a Render persistent disk can be mounted and used as the application's media directory. This is simpler but ties the media files to that Render service and is less suitable for horizontal scaling.

Mount the disk at the application's `private_media` directory and keep a separate backup strategy.

## Important

The database and media storage are separate concerns. PostgreSQL should remain the system of record for metadata while object storage holds the binary media. Never store uploaded media inside Git or the database itself.

The current `services/media_storage.py` adapter is provider-neutral. The existing legacy upload routes remain compatible while they are migrated to the adapter module domain-by-domain.
