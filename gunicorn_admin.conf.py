"""Gunicorn hooks for persistent administrator credentials."""

def post_worker_init(worker):
    import app as app_module
    from admin_runtime import install
    install(app_module.app)
