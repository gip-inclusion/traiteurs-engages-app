import logging
import logging.config
import uuid
from contextvars import ContextVar

from flask import g, request

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = _request_id.get() or "-"
        return True


LOGGING_CONFIG: dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": RequestIdFilter},
    },
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
            "rename_fields": {"asctime": "ts", "levelname": "level", "name": "logger"},
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["request_id"],
        },
    },
    "root": {"level": "INFO", "handlers": ["stdout"]},
    "loggers": {
        # Dampen gunicorn/werkzeug access logs — we already log every request.
        "werkzeug": {"level": "WARNING", "handlers": ["stdout"], "propagate": False},
        "gunicorn.access": {"level": "WARNING"},
    },
}


def configure_logging():
    logging.config.dictConfig(LOGGING_CONFIG)


def install_request_id_hooks(app):
    logger = logging.getLogger(__name__)

    @app.before_request
    def _set_request_id():
        rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
        g.request_id = rid
        _request_id.set(rid)

    @app.after_request
    def _log_request(response):
        # CSRFProtect can short-circuit before before_request fires; fall back
        # to the contextvar so the log line still carries a correlation id.
        rid = g.get("request_id") or _request_id.get() or "-"
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "user_id": str(g.get("current_user").id)
                if g.get("current_user")
                else None,
            },
        )
        response.headers.setdefault("X-Request-Id", rid)
        return response
