# University Management System

A clean Flask and SQLAlchemy foundation for account registration, institutions, role assignments, departments, courses, enrollment, notices, and health checks.

## Local setup
Use Python 3.11+. Run `python -m venv .venv`, activate it, then `pip install -r requirements.txt` and `python app.py`. Open http://127.0.0.1:5000. Set a strong `SECRET_KEY` for deployments.

## Render
Create a Blueprint from `render.yaml`. It provisions a web service and PostgreSQL database. Keep all secrets out of Git.

## Important
This is a minimal baseline, not a full enterprise SIS. Attendance, grades, admissions, password reset, email verification, uploads, and formal schema migrations are not included yet. The app initializes tables but does not migrate the old schema. Back up old data before switching. The clean rebuild removes legacy files from the new branch's current tree; Git history remains available.