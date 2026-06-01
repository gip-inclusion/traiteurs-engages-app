import logging
import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect

logger = logging.getLogger(__name__)

csrf = CSRFProtect()


def _is_truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes")


def _limiter_storage_uri() -> str:
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        base = (
            redis_url.rstrip("/").rsplit("/", 1)[0]
            if redis_url.count("/") >= 3
            else redis_url
        )
        return f"{base}/1"

    in_dev = _is_truthy_env("FLASK_DEBUG")
    explicit_opt_in = _is_truthy_env("LIMITER_ALLOW_MEMORY")
    if not (in_dev or explicit_opt_in):
        raise SystemExit(
            "Refusing to start: REDIS_URL is required outside dev/test. "
            "The in-memory rate-limiter store is per-process — in a "
            "multi-worker server it silently bypasses /login throttling. "
            "Set REDIS_URL, or LIMITER_ALLOW_MEMORY=1 if you understand "
            "the trade-off (single-worker only)."
        )
    logger.warning(
        "Rate-limiter using in-memory store — per-process counters, "
        "NOT safe behind multiple gunicorn workers."
    )
    return "memory://"


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per minute", "1000 per hour"],
    storage_uri=_limiter_storage_uri(),
    strategy="moving-window",
)
