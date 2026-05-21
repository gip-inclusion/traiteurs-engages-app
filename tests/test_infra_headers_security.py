"""Tests for the infra/headers cluster of the 2026-05-13 security audit.

Findings covered:

  * H-3  — `_limiter_storage_uri()` refuses to start on `memory://`
           outside an explicit dev / test opt-in.
  * H-11 — `gunicorn.conf.py` now sets `forwarded_allow_ips` and request
           caps so trust-chain headers (X-Forwarded-*) actually reach
           Werkzeug ProxyFix on Scalingo.
  * H-13 — `secure_cookies` defaults to True; HSTS is decoupled from
           that flag and emitted whenever the request is secure.
  * M-12 — CSP now includes `form-action 'self'`.
"""

from __future__ import annotations

import importlib
import os

import pytest


# H-3 — rate-limiter must fail closed when REDIS_URL is unset in prod


def test_limiter_refuses_memory_storage_without_marker(monkeypatch):
    # Import `extensions` before clearing env so the module body (which
    # calls `_limiter_storage_uri()` at import time on the `Limiter(...)`
    # line) runs against the conftest-blessed env. Otherwise a cold
    # import here raises SystemExit *outside* the pytest.raises block
    # and the test crashes regardless of whether the implementation is
    # correct — fragile to test ordering.
    import extensions

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    monkeypatch.delenv("LIMITER_ALLOW_MEMORY", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        extensions._limiter_storage_uri()
    assert "REDIS_URL" in str(excinfo.value)


@pytest.mark.parametrize(
    "marker, value",
    [
        ("FLASK_DEBUG", "1"),
        ("LIMITER_ALLOW_MEMORY", "1"),
        ("LIMITER_ALLOW_MEMORY", "true"),
        ("LIMITER_ALLOW_MEMORY", "YES"),
    ],
)
def test_limiter_memory_allowed_with_explicit_marker(monkeypatch, marker, value):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    monkeypatch.delenv("LIMITER_ALLOW_MEMORY", raising=False)
    monkeypatch.setenv(marker, value)

    import extensions

    assert extensions._limiter_storage_uri() == "memory://"


def test_limiter_uses_redis_db_one_when_url_is_set(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    import extensions

    assert extensions._limiter_storage_uri() == "redis://localhost:6379/1"




def _load_gunicorn_conf():
    import importlib.util
    import pathlib
    import sys

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "_gunicorn_conf_under_test", repo_root / "gunicorn.conf.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_gunicorn_conf_sets_forwarded_allow_ips_to_star_by_default(monkeypatch):
    """The gunicorn config must wire `forwarded_allow_ips` to '*' (the
    Scalingo-safe default) when no FORWARDED_ALLOW_IPS override is set.
    The audit's PoC: without this, `X-Forwarded-For` is stripped before
    ProxyFix can read it, every request's `remote_addr` collapses to
    the router IP, and rate-limit buckets fuse across all clients."""
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    conf = _load_gunicorn_conf()
    assert conf.forwarded_allow_ips == "*", (
        f"default must be '*' for the Scalingo path; got {conf.forwarded_allow_ips!r}"
    )


def test_gunicorn_conf_honors_forwarded_allow_ips_override(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.0.0/24,192.168.1.1")
    conf = _load_gunicorn_conf()
    assert conf.forwarded_allow_ips == "10.0.0.0/24,192.168.1.1"


def test_gunicorn_conf_caps_request_line_and_field_size():
    conf = _load_gunicorn_conf()
    assert conf.limit_request_line >= 4096
    assert conf.limit_request_field_size >= 8192




def test_csp_includes_form_action_self(client):
    """Audit M-12: without `form-action 'self'`, an injected
    `<form action="https://evil/">` would POST credentials off-origin
    before any other defense layer could complain."""
    resp = client.get("/")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "form-action 'self'" in csp, f"CSP must lock form action to self; got: {csp}"




def test_secure_cookies_defaults_to_true_when_env_is_absent(monkeypatch):
    """Audit H-13: the Pydantic default must be True so a self-host
    operator who forgets the env var still gets Secure cookies + HSTS.
    Empty-string env (the docker-compose interpolation pattern) keeps
    coercing to False for local dev."""
    # Wipe the cached settings module so we re-evaluate the field default.
    monkeypatch.delenv("SECURE_COOKIES", raising=False)
    import config as config_module

    # Reload to re-apply field defaults under the clean env.
    importlib.reload(config_module)
    fresh_settings = config_module.Settings()
    assert fresh_settings.secure_cookies is True, (
        "default must be True; got False — H-13 regression"
    )


def test_secure_cookies_empty_env_still_coerces_to_false(monkeypatch):
    monkeypatch.setenv("SECURE_COOKIES", "")
    import config as config_module

    importlib.reload(config_module)
    fresh_settings = config_module.Settings()
    assert fresh_settings.secure_cookies is False


def test_hsts_emitted_for_secure_requests(app):
    """Audit H-13: HSTS now goes out whenever the request is actually
    TLS, regardless of the `secure_cookies` flag. We simulate by
    overriding `is_secure` via WSGI environ."""
    # Force a "TLS-looking" request via WSGI environ. ProxyFix isn't in
    # the path of the test client but `request.is_secure` reads
    # `wsgi.url_scheme` directly.
    client = app.test_client()
    resp = client.get("/", environ_overrides={"wsgi.url_scheme": "https"})
    assert "Strict-Transport-Security" in resp.headers, (
        "HSTS must be emitted on a TLS request, even when secure_cookies "
        "is False (the case after H-13's decoupling)"
    )


def test_hsts_skipped_for_plain_http_when_secure_cookies_false(app):
    # conftest sets SECURE_COOKIES=false explicitly for tests; this
    # asserts that combo behaves as documented.
    assert os.environ.get("SECURE_COOKIES", "").lower() == "false"

    client = app.test_client()
    resp = client.get("/", environ_overrides={"wsgi.url_scheme": "http"})
    assert "Strict-Transport-Security" not in resp.headers, (
        "HSTS must be skipped on plain HTTP when secure_cookies is False"
    )
