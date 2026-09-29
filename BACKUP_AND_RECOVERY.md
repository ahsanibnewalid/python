# UniversityConnect backup and recovery

## Database
Use PostgreSQL for production. Keep scheduled provider backups plus an independent logical export.

Manual export:
```bash
pg_dump --dbname="$DATABASE_URL" --format=custom --no-owner --no-privileges --file="universityconnect-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

Never commit DATABASE_URL.

Restore a disposable database first:
```bash
pg_restore --dbname="$RESTORE_DATABASE_URL" --clean --if-exists --no-owner --no-privileges backup.dump
```

After restore, verify login, profiles, posts, messaging, groups, campus data, admin access and tenant isolation.

## Media
The application now supports configurable STORAGE_ROOT. If local media is used on Render, attach a persistent disk and point STORAGE_ROOT at its mount path. Before running multiple instances, migrate media to S3/R2-compatible object storage.

## Recovery drill
Perform a restore test at least monthly. Record backup timestamp, restore duration, application redeploy result, media availability and tenant-isolation test results.
