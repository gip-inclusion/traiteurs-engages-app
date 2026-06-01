from __future__ import annotations

import importlib
import os

import pytest


def test_limiter_refuses_memory_storage_without_marker(monkeypatch):
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
    resp = client.get("/")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "form-action 'self'" in csp, f"CSP must lock form action to self; got: {csp}"


def test_csp_form_action_whitelists_stripe_connect(client):
    resp = client.get("/")
    csp = resp.headers.get("Content-Security-Policy", "")
    form_action = next(
        (d.strip() for d in csp.split(";") if d.strip().startswith("form-action")),
        "",
    )
    assert "https://connect.stripe.com" in form_action, (
        f"connect.stripe.com must be in form-action; got: {form_action!r}"
    )


def test_secure_cookies_defaults_to_true_when_env_is_absent(monkeypatch):
    monkeypatch.delenv("SECURE_COOKIES", raising=False)
    import config as config_module

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
    client = app.test_client()
    resp = client.get("/", environ_overrides={"wsgi.url_scheme": "https"})
    assert "Strict-Transport-Security" in resp.headers, (
        "HSTS must be emitted on a TLS request, even when secure_cookies "
        "is False (the case after H-13's decoupling)"
    )


def test_hsts_skipped_for_plain_http_when_secure_cookies_false(app):
    assert os.environ.get("SECURE_COOKIES", "").lower() == "false"

    client = app.test_client()
    resp = client.get("/", environ_overrides={"wsgi.url_scheme": "http"})
    assert "Strict-Transport-Security" not in resp.headers, (
        "HSTS must be skipped on plain HTTP when secure_cookies is False"
    )
