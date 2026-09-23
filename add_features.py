"""Database migration helper for University Connect.

The old version of this file contained a truncated generated Python string and
could not compile. The application now owns its schema migrations in init_db().
Run this script after setting the same environment variables used by the app.
"""
import app

if __name__ == "__main__":
    app.init_db()
    print("University Connect database schema is up to date.")
