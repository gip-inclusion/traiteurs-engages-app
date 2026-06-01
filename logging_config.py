from __future__ import annotations

import logging
import logging.config
import os
import time
import uuid
from contextvars import ContextVar
from typing import Any

from flask import g, got_request_exception, has_request_context, request
from sqlalchemy import event

_TRACE_ID: ContextVar[str | None] = ContextVar("trace_id", default=None)
_SPAN_ID: ContextVar[str | None] = ContextVar("span_id", default=None)
_BOUND: ContextVar[dict[str, Any] | None] = ContextVar("bound_context", default=None)


def _bound() -> dict[str, Any]:
    return _BOUND.get() or {}


_HEX = set("0123456789abcdef")


def _client_ip() -> str | None:
    from config import settings

    if settings.trust_proxy_headers:
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            first = xff.split(",", 1)[0].strip()
            if first:
                return first
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
    return request.remote_addr


def _new_trace_id() -> str:
    return uuid.uuid4().hex


def _new_span_id() -> str:
    return uuid.uuid4().hex[:16]


def _all_hex(s: str) -> bool:
    return bool(s) and all(c in _HEX for c in s.lower())


def _parse_traceparent(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    parts = value.split("-")
    if len(parts) != 4 or parts[0] != "00":
        return None, None
    tid, sid = parts[1], parts[2]
    if len(tid) == 32 and _all_hex(tid) and tid != "0" * 32:
        if len(sid) == 16 and _all_hex(sid) and sid != "0" * 16:
            return tid.lower(), sid.lower()
    return None, None


def _resolve_inbound_trace(
    traceparent: str | None, x_request_id: str | None
) -> tuple[str, str | None]:
    tid, parent_sid = _parse_traceparent(traceparent)
    if tid:
        return tid, parent_sid
    legacy = (x_request_id or "").strip().lower()
    if 12 <= len(legacy) <= 64 and _all_hex(legacy):
        return legacy.ljust(32, "0")[:32], None
    return _new_trace_id(), None


class TraceContextMiddleware:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        traceparent = environ.get("HTTP_TRACEPARENT")
        x_request_id = environ.get("HTTP_X_REQUEST_ID")
        tid, parent_sid = _resolve_inbound_trace(traceparent, x_request_id)
        _TRACE_ID.set(tid)
        _SPAN_ID.set(_new_span_id())
        _BOUND.set({})
        if parent_sid:
            environ["traceperf.parent_span_id"] = parent_sid
        return self.wsgi_app(environ, start_response)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _TRACE_ID.get() or "-"
        record.span_id = _SPAN_ID.get() or "-"
        record.request_id = record.trace_id

        live_user_id: str | None = None
        live_ip: str | None = None
        if has_request_context():
            live_ip = _client_ip()
            user = g.get("current_user")
            if user is not None:
                uid = getattr(user, "id", None)
                if uid:
                    live_user_id = str(uid)

        bound = _bound()
        if not hasattr(record, "user_id"):
            record.user_id = bound.get("user_id", live_user_id)
        if not hasattr(record, "ip"):
            record.ip = bound.get("ip", live_ip)

        for key, value in bound.items():
            if key in ("user_id", "ip"):
                continue
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

LOGGING_CONFIG: dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "context": {"()": ContextFilter},
    },
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": (
                "%(asctime)s %(levelname)s %(name)s "
                "%(trace_id)s %(span_id)s %(message)s"
            ),
            "rename_fields": {
                "asctime": "ts",
                "levelname": "level",
                "name": "logger",
            },
            "timestamp": True,
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["context"],
        },
    },
    "root": {"level": _LOG_LEVEL, "handlers": ["stdout"], "filters": ["context"]},
    "loggers": {
        "werkzeug": {"level": "WARNING", "handlers": ["stdout"], "propagate": False},
        "gunicorn.access": {"level": "WARNING"},
        "sqlalchemy.engine": {"level": "WARNING"},
    },
}


def configure_logging() -> None:
    logging.config.dictConfig(LOGGING_CONFIG)


def bind(**fields: Any) -> None:
    current = dict(_bound())
    current.update({k: v for k, v in fields.items() if v is not None})
    _BOUND.set(current)


def unbind(*keys: str) -> None:
    current = dict(_bound())
    for k in keys:
        current.pop(k, None)
    _BOUND.set(current)


def get_trace_context() -> dict[str, str | None]:
    return {"trace_id": _TRACE_ID.get(), "span_id": _SPAN_ID.get()}


def set_trace_context(trace_id: str | None, span_id: str | None = None) -> None:
    _TRACE_ID.set(trace_id or _new_trace_id())
    _SPAN_ID.set(span_id or _new_span_id())


def reset_trace_context() -> None:
    _TRACE_ID.set(None)
    _SPAN_ID.set(None)
    _BOUND.set({})


def install_request_id_hooks(app) -> None:
    app.wsgi_app = TraceContextMiddleware(app.wsgi_app)

    req_logger = logging.getLogger("app.request")
    exc_logger = logging.getLogger("app.exception")

    @app.before_request
    def _start_trace():
        tid = _TRACE_ID.get() or _new_trace_id()
        sid = _SPAN_ID.get() or _new_span_id()
        g.trace_id = tid
        g.span_id = sid
        g.request_id = tid
        g.parent_span_id = request.environ.get("traceperf.parent_span_id")
        g.request_started_at = time.perf_counter()
        g.sql_query_count = 0
        g.sql_total_ms = 0.0

    @app.after_request
    def _log_request(response):
        tid = _TRACE_ID.get() or g.get("trace_id") or _new_trace_id()
        started = g.get("request_started_at")
        duration_ms = (
            round((time.perf_counter() - started) * 1000, 2)
            if started is not None
            else None
        )
        extra: dict[str, Any] = {
            "event": "http_request",
            "http": {
                "method": request.method,
                "url": request.path,
                "status_code": response.status_code,
                "useragent": request.headers.get("User-Agent"),
                "referer": request.headers.get("Referer"),
            },
            "endpoint": request.endpoint,
            "duration_ms": duration_ms,
            "req_bytes": request.content_length,
            "sql_queries": g.get("sql_query_count", 0),
            "sql_ms": round(g.get("sql_total_ms", 0.0), 2),
        }
        parent_sid = g.get("parent_span_id")
        if parent_sid:
            extra["parent_span_id"] = parent_sid
        user = g.get("current_user")
        if user is not None:
            role = getattr(user, "role", None)
            extra["user_role"] = getattr(role, "value", None) or (
                str(role) if role is not None else None
            )
            cid = getattr(user, "company_id", None)
            if cid:
                extra["company_id"] = str(cid)
        req_logger.info("request", extra=extra)
        response.headers.setdefault("X-Request-Id", tid)
        response.headers.setdefault("X-Trace-Id", tid)
        return response

    def _on_unhandled(_sender, exception, **_kw):
        exc_logger.exception(
            "unhandled_exception",
            extra={
                "event": "unhandled_exception",
                "endpoint": request.endpoint,
                "method": request.method,
                "path": request.path,
                "exc_type": exception.__class__.__name__,
            },
        )

    got_request_exception.connect(_on_unhandled, app)


def install_sql_tracing(engine) -> None:
    sql_logger = logging.getLogger("app.sql")
    slow_threshold_ms = float(os.getenv("LOG_SLOW_QUERY_MS", "500"))

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        context._traceperf_start = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        start = getattr(context, "_traceperf_start", None)
        if start is None:
            return
        elapsed_ms = (time.perf_counter() - start) * 1000
        try:
            g.sql_query_count = g.get("sql_query_count", 0) + 1
            g.sql_total_ms = g.get("sql_total_ms", 0.0) + elapsed_ms
        except RuntimeError:
            pass
        if elapsed_ms >= slow_threshold_ms:
            sql_logger.warning(
                "slow_query",
                extra={
                    "event": "slow_query",
                    "duration_ms": round(elapsed_ms, 2),
                    "statement": (statement or "")[:500],
                },
            )
