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
    assert "FLASK_DEBUG" in err


@pytest.mark.parametrize(
    "marker, value",
    [
        ("FLASK_DEBUG", "1"),
        ("FLASK_DEBUG", "true"),
        ("FLASK_DEBUG", "yes"),
        ("SEED_FIXTURES_ALLOW", "1"),
        ("SEED_FIXTURES_ALLOW", "TRUE"),
    ],
    ids=["debug=1", "debug=true", "debug=yes", "allow=1", "allow=TRUE"],
)
def test_seed_guard_lifts_with_marker(seed_data_module, monkeypatch, marker, value):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    monkeypatch.delenv("SEED_FIXTURES_ALLOW", raising=False)
    monkeypatch.setenv(marker, value)

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
