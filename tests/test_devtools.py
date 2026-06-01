import os
import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("ENABLE_DEMO_SEED") != "1",
    reason="dev switcher only registered when ENABLE_DEMO_SEED=1",
)


def test_switch_account_rejects_unknown_email(client):
    resp = client.post(
        "/dev/switch-account",
        data={"email": "intruder@evil.example.com"},
    )
    assert resp.status_code == 403


def test_switch_account_requires_post(client):
    resp = client.get("/dev/switch-account")
    assert resp.status_code == 405
