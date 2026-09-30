"""Gunicorn hooks for persistent administrator credentials."""

def post_worker_init(worker):
    from admin_runtime import install
    install(worker.app.wsgi)
