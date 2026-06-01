import os

_RELOAD = os.getenv("GUNICORN_RELOAD") == "1"

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
workers = int(os.getenv("WEB_CONCURRENCY", "1" if _RELOAD else "4"))
threads = int(os.getenv("GUNICORN_THREADS", "2"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))
reload = _RELOAD
preload_app = not _RELOAD

forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "*")

limit_request_line = int(os.getenv("GUNICORN_LIMIT_REQUEST_LINE", "8192"))
limit_request_field_size = int(os.getenv("GUNICORN_LIMIT_REQUEST_FIELD_SIZE", "16384"))


def post_fork(server, worker):
    import sys

    db = sys.modules.get("database")
    if db is not None:
        db.engine.dispose(close=False)
