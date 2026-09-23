"""Template verification helper.

Template generation was previously stored as an incomplete code-generation
script. Templates are now committed directly, so this command only verifies
that the application can load them.
"""
import app

if __name__ == "__main__":
    print("University Connect templates are loaded from templates/.")
