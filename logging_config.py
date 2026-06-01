"""Structured JSON logging with trace correlation.

Every log record carries a `trace_id` (W3C-compatible 32-hex) and a
`span_id` (16-hex), propagated via contextvars so it works across Flask
requests, Dramatiq workers, CLI commands and ad-hoc threads sharing the
context. HTTP requests honour an incoming `traceparent` header (or the
legacy `X-Request-Id`) so traces stitch across services.
"""

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
# Default is None (not `{}`) so we can never accidentally mutate a shared
# default-dict instance — readers fall back to a fresh empty mapping.
_BOUND: ContextVar[dict[str, Any] | None] = ContextVar("bound_context", default=None)


def _bound() -> dict[str, Any]:
    return _BOUND.get() or {}


_HEX = set("0123456789abcdef")


def _client_ip() -> str | None:
    """Best-effort client IP.

    Gated on `settings.trust_proxy_headers` — the same flag that controls
    ProxyFix. Without it, XFF and X-Real-IP are client-controlled headers
    and reading them would let anyone forge their logged IP (`.env.example`
    spells this out). With it, we trust the upstream proxy and pick the
    left-most XFF entry as the original client.

    When the flag is on but XFF is absent, fall back to `remote_addr` —
    which ProxyFix has already rewritten from the trusted XFF.
    """
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
    # Untrusted deployments: peer TCP address, not spoofable from the wire.
    return request.remote_addr


def _new_trace_id() -> str:
    return uuid.uuid4().hex


def _new_span_id() -> str:
    return uuid.uuid4().hex[:16]


def _all_hex(s: str) -> bool:
    return bool(s) and all(c in _HEX for c in s.lower())


def _parse_traceparent(value: str | None) -> tuple[str | None, str | None]:
    # W3C traceparent: "00-<32 hex>-<16 hex>-<flags>"
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
    """Return (trace_id, parent_span_id) from inbound headers, minting if absent."""
    tid, parent_sid = _parse_traceparent(traceparent)
    if tid:
        return tid, parent_sid
    legacy = (x_request_id or "").strip().lower()
    if 12 <= len(legacy) <= 64 and _all_hex(legacy):
        return legacy.ljust(32, "0")[:32], None
    return _new_trace_id(), None


class TraceContextMiddleware:
    """WSGI middleware that initialises trace context at the very start of
    every request — *before* Flask's before_request hooks run.

    Without this, a CSRFProtect rejection (or any before_request that
    short-circuits) would skip our `_start_trace` and leave the worker's
    `_BOUND`/`_TRACE_ID` contextvars carrying values from the *previous*
    request handled by the same thread. The result was cross-request
    pollution of `user_id` and trace ids in the log line emitted by
    `_log_request` for the rejected request.

    Resetting in WSGI guarantees a clean slate regardless of which Flask
    hooks fire downstream.
    """

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
    """Stamps every record with trace/span ids, the active user, and the
    caller IP — so any `logger.info(...)` line carries enough context to be
    pivotted on without the caller having to remember to add `extra=`."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _TRACE_ID.get() or "-"
        record.span_id = _SPAN_ID.get() or "-"
        record.request_id = record.trace_id  # back-compat alias

        # Resolve user_id and ip from the live Flask request if we have one.
        # Outside a request (CLI, Dramatiq worker, threads), they fall back to
        # whatever bind() left in the context — None otherwise.
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
        # Precedence: explicit `extra=` on the call site wins; then bind();
        # then the live request scan; then None. This lets callers override
        # (e.g. a worker re-hydrating user_id from a message arg).
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
    # Filter is duplicated on the root logger so caplog (and any other
    # propagation-listening handler we wire later) sees records with the
    # context fields already stamped. The filter is idempotent — it only
    # writes attrs that aren't already set — so running it twice is cheap
    # and safe.
    "root": {"level": _LOG_LEVEL, "handlers": ["stdout"], "filters": ["context"]},
    "loggers": {
        # We log every request ourselves; werkzeug/gunicorn access lines are
        # noise on top of that.
        "werkzeug": {"level": "WARNING", "handlers": ["stdout"], "propagate": False},
        "gunicorn.access": {"level": "WARNING"},
        # SQLAlchemy emits its own INFO chatter we don't want — slow queries
        # are emitted by install_sql_tracing.
        "sqlalchemy.engine": {"level": "WARNING"},
    },
}


def configure_logging() -> None:
    logging.config.dictConfig(LOGGING_CONFIG)


def bind(**fields: Any) -> None:
    """Add fields to every subsequent log record in the current context.

    >>> bind(order_id=str(order.id))
    >>> logger.info("invoice_sent")  # JSON line carries order_id automatically
    """
    current = dict(_bound())
    current.update({k: v for k, v in fields.items() if v is not None})
    _BOUND.set(current)


def unbind(*keys: str) -> None:
    current = dict(_bound())
    for k in keys:
        current.pop(k, None)
    _BOUND.set(current)


def get_trace_context() -> dict[str, str | None]:
    """Snapshot the active trace, for cross-process propagation."""
    return {"trace_id": _TRACE_ID.get(), "span_id": _SPAN_ID.get()}


def set_trace_context(trace_id: str | None, span_id: str | None = None) -> None:
    _TRACE_ID.set(trace_id or _new_trace_id())
    _SPAN_ID.set(span_id or _new_span_id())


def reset_trace_context() -> None:
    _TRACE_ID.set(None)
    _SPAN_ID.set(None)
    _BOUND.set({})


def install_request_id_hooks(app) -> None:
    """Wire request lifecycle, trace propagation and unhandled-exception logs.

    Installs a WSGI middleware so trace context is initialised even when a
    `before_request` hook (e.g. CSRFProtect) short-circuits — see
    `TraceContextMiddleware` for the leak this prevents.
    """
    app.wsgi_app = TraceContextMiddleware(app.wsgi_app)

    req_logger = logging.getLogger("app.request")
    exc_logger = logging.getLogger("app.exception")

    @app.before_request
    def _start_trace():
        # Trace ids were minted by TraceContextMiddleware; we only stash
        # them on `g` for code that reads them directly + the request
        # lifecycle metrics.
        tid = _TRACE_ID.get() or _new_trace_id()
        sid = _SPAN_ID.get() or _new_span_id()
        g.trace_id = tid
        g.span_id = sid
        g.request_id = tid  # back-compat for code reading g.request_id
        g.parent_span_id = request.environ.get("traceperf.parent_span_id")
        g.request_started_at = time.perf_counter()
        g.sql_query_count = 0
        g.sql_total_ms = 0.0

    @app.after_request
    def _log_request(response):
        # Trace context is guaranteed set by the WSGI middleware, so reads
        # from contextvars are always populated even when `_start_trace`
        # didn't run (CSRF reject before before_request).
        tid = _TRACE_ID.get() or g.get("trace_id") or _new_trace_id()
        started = g.get("request_started_at")
        duration_ms = (
            round((time.perf_counter() - started) * 1000, 2)
            if started is not None
            else None
        )
        # user_id and ip are stamped on every record by ContextFilter, so we
        # don't duplicate them here — only the http_request-line-specific
        # fields go through `extra`. The `http` sub-dict follows the
        # OpenTelemetry / Datadog Standard Attributes convention so out-of-
        # the-box dashboards work; `duration_ms` is kept (unit in name,
        # vendor-neutral) rather than converted to ns/seconds. `status` was
        # renamed to `http.status_code` to avoid colliding with Datadog's
        # reserved `status` field (log severity).
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
        # got_request_exception fires before Flask's error handler runs and
        # does not interfere with response generation, so we keep the
        # existing @errorhandler(500) intact and only add a structured log.
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
    """Time every cursor execute and surface slow queries.

    Aggregates per-request counters into `g` when a Flask request context is
    live; outside a request (CLI, worker) it just emits slow-query warnings.
    """
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
        # Aggregate into request-scoped counters if we're inside a request.
        try:
            g.sql_query_count = g.get("sql_query_count", 0) + 1
            g.sql_total_ms = g.get("sql_total_ms", 0.0) + elapsed_ms
        except RuntimeError:
            # Outside Flask app/request context — nothing to aggregate into.
            pass
        if elapsed_ms >= slow_threshold_ms:
            sql_logger.warning(
                "slow_query",
                extra={
                    "event": "slow_query",
                    "duration_ms": round(elapsed_ms, 2),
                    # Truncated; full statement would balloon log volume and
                    # may carry bind values through the formatter.
                    "statement": (statement or "")[:500],
                },
            )
