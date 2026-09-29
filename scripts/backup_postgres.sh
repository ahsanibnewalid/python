#!/usr/bin/env bash
set -euo pipefail
: "${DATABASE_URL:?DATABASE_URL is required}"
OUT_DIR="${BACKUP_DIR:-./backups}"
mkdir -p "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
pg_dump --dbname="$DATABASE_URL" --format=custom --no-owner --no-privileges --file="$OUT_DIR/universityconnect-$STAMP.dump"
echo "Created $OUT_DIR/universityconnect-$STAMP.dump"
