import importlib


def _reload_extensions():
    import extensions

    return importlib.reload(extensions)


def test_limiter_uses_memory_when_redis_url_unset(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    ext = _reload_extensions()
    assert ext.limiter._storage_uri == "memory://", (
        "Without REDIS_URL the limiter should fall back to in-memory storage"
    )


def test_limiter_uses_redis_db1_when_redis_url_set(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    ext = _reload_extensions()
    assert ext.limiter._storage_uri == "redis://redis:6379/1", (
        f"Expected redis://redis:6379/1, got {ext.limiter._storage_uri}"
    )


def test_limiter_handles_redis_url_without_db_suffix(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379")
    ext = _reload_extensions()
    assert ext.limiter._storage_uri == "redis://redis:6379/1"


def test_limiter_strategy_is_moving_window():
    from extensions import limiter

    assert limiter._strategy == "moving-window"
