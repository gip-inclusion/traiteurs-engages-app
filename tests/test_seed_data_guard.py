"""Tests for the production guard around `seed_data.seed()`.

Audit C-3 (2026-05-13): the seeder used to be wired into the Procfile
postdeploy chain via `if [ "$SEED_FIXTURES" = "true" ]`. That branch
has been removed, but to stop a stray `scalingo run python
seed_data.py` from re-opening the same hole, `seed()` now refuses to
run unless an explicit dev marker is in the environment. These tests
hold the guard in place — flip its behaviour and they fail loudly.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def seed_data_module(monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    monkeypatch.delenv("SEED_FIXTURES_ALLOW", raising=False)
    import seed_data

    return importlib.reload(seed_data)


def test_seed_refuses_without_dev_marker(seed_data_module, monkeypatch, capsys):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    monkeypatch.delenv("SEED_FIXTURES_ALLOW", raising=False)

    with pytest.raises(SystemExit) as exc:
        seed_data_module.seed()
    assert exc.value.code == 2, "non-2 exit codes get swallowed by some runners"

    err = capsys.readouterr().err
    assert "refuses to run" in err
    assert "FLASK_DEBUG" in err  # operator must see what to set


@pytest.mark.parametrize(
    "marker, value",
    [
        ("FLASK_DEBUG", "1"),
        ("FLASK_DEBUG", "true"),
        ("FLASK_DEBUG", "yes"),
        ("SEED_FIXTURES_ALLOW", "1"),
        ("SEED_FIXTURES_ALLOW", "TRUE"),  # case-insensitive
    ],
    ids=["debug=1", "debug=true", "debug=yes", "allow=1", "allow=TRUE"],
)
def test_seed_guard_lifts_with_marker(seed_data_module, monkeypatch, marker, value):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    monkeypatch.delenv("SEED_FIXTURES_ALLOW", raising=False)
    monkeypatch.setenv(marker, value)

    # Just the guard, not the full seed — running the full seed would
    # require a live DB and is exercised by other tests via conftest.
    seed_data_module._refuse_in_production()


@pytest.mark.parametrize(
    "value",
    ["", "0", "false", "no", "off", "production"],
    ids=["empty", "0", "false", "no", "off", "production"],
)
def test_seed_guard_rejects_falsy_marker(seed_data_module, monkeypatch, value):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    monkeypatch.delenv("SEED_FIXTURES_ALLOW", raising=False)
    monkeypatch.setenv("FLASK_DEBUG", value)

    with pytest.raises(SystemExit):
        seed_data_module._refuse_in_production()
