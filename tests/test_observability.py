"""Regression tests for the structured-logging / trace-context layer."""

import logging

import pytest

from logging_config import (
    _BOUND,
    _SPAN_ID,
    _TRACE_ID,
    TraceContextMiddleware,
    _client_ip,
)


VALID_TRACE_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
VALID_SPAN_ID = "bbbbbbbbbbbbbbbb"
VALID_TRACEPARENT = f"00-{VALID_TRACE_ID}-{VALID_SPAN_ID}-01"


def _capture_record(caplog, event):
    """Return the first record whose `event` extra matches."""
    for r in caplog.records:
        if getattr(r, "event", None) == event:
            return r
    return None


def test_traceparent_header_propagates_to_log_and_response(app, client, caplog):
    with caplog.at_level(logging.INFO, logger="app.request"):
        resp = client.get("/health", headers={"traceparent": VALID_TRACEPARENT})

    assert resp.status_code == 200
    assert resp.headers.get("X-Trace-Id") == VALID_TRACE_ID
    assert resp.headers.get("X-Request-Id") == VALID_TRACE_ID

    rec = _capture_record(caplog, "http_request")
    assert rec is not None, "no http_request log emitted"
    assert rec.trace_id == VALID_TRACE_ID
    assert getattr(rec, "parent_span_id", None) == VALID_SPAN_ID


def test_unknown_traceparent_mints_fresh_trace(app, client):
    # Missing header → middleware mints a 32-hex trace id we can verify
    # round-trips into the response.
    resp = client.get("/health")
    tid = resp.headers.get("X-Trace-Id")
    assert tid is not None
    assert len(tid) == 32
    assert all(c in "0123456789abcdef" for c in tid)


def test_wsgi_middleware_clears_stale_bound_before_request(app):
    """Regression: a bind() left over from a previous request handled by
    the same worker thread must not bleed into the next request, even if
    Flask's before_request hooks short-circuit (CSRF reject, etc.).
    """
    # Simulate a stale context from a previous request.
    _BOUND.set({"leaked_user_id": "alice"})
    _TRACE_ID.set("stale-trace-id-from-prior-request")

    captured: dict = {}

    def fake_downstream(environ, start_response):
        captured["bound"] = dict(_BOUND.get())
        captured["trace"] = _TRACE_ID.get()
        start_response("200 OK", [])
        return [b""]

    middleware = TraceContextMiddleware(fake_downstream)
    middleware({"PATH_INFO": "/", "REQUEST_METHOD": "GET"}, lambda *a, **kw: None)

    assert captured["bound"] == {}, "_BOUND leaked from prior request"
    assert captured["trace"] != "stale-trace-id-from-prior-request"
    assert captured["trace"] is not None and len(captured["trace"]) == 32


def test_bind_does_not_leak_between_test_client_requests(app, client, caplog):
    """End-to-end version of the above through Flask's test client."""
    # Pollute the worker-thread context as if from a prior request.
    _BOUND.set({"unique_marker_xyz": "from_prior_request"})

    with caplog.at_level(logging.INFO, logger="app.request"):
        client.get("/health")

    rec = _capture_record(caplog, "http_request")
    assert rec is not None
    assert not hasattr(rec, "unique_marker_xyz"), (
        "bound field from a prior context leaked into the next request's log"
    )


def test_context_filter_stamps_ip_user_id_trace_on_request_log(app, client, caplog):
    """The http_request log line emitted by `_log_request` should carry
    `ip`, `user_id` and a real `trace_id` even though the call site no
    longer passes them via `extra=` — the ContextFilter is responsible."""
    with caplog.at_level(logging.INFO, logger="app.request"):
        client.get("/health", environ_base={"REMOTE_ADDR": "10.0.0.7"})

    rec = _capture_record(caplog, "http_request")
    assert rec is not None
    assert rec.ip == "10.0.0.7"
    assert rec.user_id is None  # anonymous request
    assert rec.trace_id and rec.trace_id != "-"


def test_client_ip_ignores_xff_without_trust_proxy(app, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "trust_proxy_headers", False)
    with app.test_request_context(
        "/",
        headers={"X-Forwarded-For": "1.2.3.4"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    ):
        assert _client_ip() == "127.0.0.1"


def test_client_ip_honors_xff_when_proxy_trusted(app, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    with app.test_request_context(
        "/",
        headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    ):
        assert _client_ip() == "1.2.3.4"


def test_client_ip_falls_back_to_x_real_ip_when_no_xff(app, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    with app.test_request_context(
        "/",
        headers={"X-Real-IP": "9.9.9.9"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    ):
        assert _client_ip() == "9.9.9.9"


@pytest.fixture(autouse=True)
def _reset_contextvars():
    """Each test starts with a pristine trace context — otherwise stale
    state from one test would bleed into the next on the same pytest
    worker thread (which is exactly the bug TraceContextMiddleware fixes
    in production)."""
    _TRACE_ID.set(None)
    _SPAN_ID.set(None)
    _BOUND.set({})
    yield
    _TRACE_ID.set(None)
    _SPAN_ID.set(None)
    _BOUND.set({})
