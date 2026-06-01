import time
import pytest


@pytest.mark.timing
def test_login_response_time_is_constant_for_unknown_email(client, login):

    def _measure(email: str, samples: int = 5) -> float:
        ts = []
        for _ in range(samples):
            t0 = time.perf_counter()
            client.post("/login", data={"email": email, "password": "x" * 16})
            ts.append(time.perf_counter() - t0)
        ts.sort()
        return sum(ts[1:-1]) / len(ts[1:-1])

    known = _measure("alice@test.local")
    unknown = _measure("nope_8a3f@example.invalid")

    delta = abs(known - unknown)
    assert delta < 0.10, (
        f"Login timing leak detected: {delta * 1000:.1f} ms between "
        f"known ({known * 1000:.0f} ms) and unknown ({unknown * 1000:.0f} ms) "
        "emails. An attacker can enumerate accounts via response timing."
    )
