# Fork-safety: with --preload the SQLAlchemy engine is created in the master
# and forked into workers; post_fork discards each worker's inherited pool
# refs (close=False keeps the master's live sockets up) so workers open
# fresh connections on first checkout. GUNICORN_RELOAD=1 disables preload
# because gunicorn's auto-reload re-imports the app per worker.
import os

_RELOAD = os.getenv("GUNICORN_RELOAD") == "1"

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
workers = int(os.getenv("WEB_CONCURRENCY", "1" if _RELOAD else "4"))
threads = int(os.getenv("GUNICORN_THREADS", "2"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))
reload = _RELOAD
preload_app = not _RELOAD

# Audit H-11: without this, gunicorn strips X-Forwarded-* from any
# non-loopback source — collapsing every Scalingo request's remote_addr
# to the router's IP and gluing all clients into one rate-limit bucket.
# `*` is safe only behind a managed proxy that rewrites these headers;
# self-hosted setups behind a less-trusted proxy must pin the CIDR.
forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "*")

# Defuse trivial DoS via overlong URIs/headers; the longest URL the app
# emits (signed S3 presigns) sits around 2 KB.
limit_request_line = int(os.getenv("GUNICORN_LIMIT_REQUEST_LINE", "8192"))
limit_request_field_size = int(os.getenv("GUNICORN_LIMIT_REQUEST_FIELD_SIZE", "16384"))


def post_fork(server, worker):
    import sys

    db = sys.modules.get("database")
    if db is not None:
        db.engine.dispose(close=False)
