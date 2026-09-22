#!/usr/bin/env bash
set -e
# Set APP_ENV=production and all required secrets before running this command.
exec gunicorn --workers 3 --bind 127.0.0.1:8000 app:app
