"""Gunicorn hooks for the production admin system.

The production bootstrap process runs before Gunicorn, but Gunicorn workers
import ``app:app`` in a fresh interpreter.  Therefore every worker must install
both the persistent credential routes and the System Owner command center on
its own Flask application object.
"""


def post_worker_init(worker):
    import app as app_module

    from admin_runtime import install as install_admin_runtime
    install_admin_runtime(app_module.app)

    from admin_god import install as install_admin_god
    install_admin_god(
        app_module.app,
        app_module.get_db_connection,
        getattr(app_module, "init_db", None),
    )
