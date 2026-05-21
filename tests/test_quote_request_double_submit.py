
import re
import uuid


_REDIRECT_QR_ID = re.compile(r"/client/requests/([0-9a-f-]{36})")


def _qr_id_from_redirect(response) -> uuid.UUID | None:
    location = response.headers.get("Location", "")
    m = _REDIRECT_QR_ID.search(location)
    return uuid.UUID(m.group(1)) if m else None


def _delete_quote_requests(ids):
    if not ids:
        return
    from database import session_factory
    from models import QuoteRequest

    s = session_factory()
    try:
        s.execute(QuoteRequest.__table__.delete().where(QuoteRequest.id.in_(list(ids))))
        s.commit()
    finally:
        s.close()


def _count_quote_requests() -> int:
    from sqlalchemy import func, select

    from database import session_factory
    from models import QuoteRequest

    s = session_factory()
    try:
        return s.scalar(select(func.count(QuoteRequest.id))) or 0
    finally:
        s.close()


def _minimal_wizard_payload(form_token: str | None) -> dict:
    payload: dict = {}
    if form_token is not None:
        payload["form_token"] = form_token
    return payload


def test_same_form_token_replayed_creates_only_one_quote_request(client, login):
    login("alice@test.local")
    token = str(uuid.uuid4())
    baseline = _count_quote_requests()
    created_ids: set[uuid.UUID] = set()

    try:
        first = client.post(
            "/client/requests/new",
            data=_minimal_wizard_payload(token),
            follow_redirects=False,
        )
        assert first.status_code == 302, (
            f"first wizard submission must redirect to the new detail; got "
            f"{first.status_code}"
        )
        first_id = _qr_id_from_redirect(first)
        assert first_id is not None, (
            f"first redirect must point at /client/requests/<uuid>; "
            f"got Location={first.headers.get('Location')!r}"
        )
        created_ids.add(first_id)
        assert _count_quote_requests() == baseline + 1, (
            "first submit should have created exactly one new QuoteRequest"
        )

        second = client.post(
            "/client/requests/new",
            data=_minimal_wizard_payload(token),
            follow_redirects=False,
        )
        assert second.status_code == 302, (
            f"replay must redirect to the existing detail; got {second.status_code}"
        )
        second_id = _qr_id_from_redirect(second)
        assert second_id == first_id, (
            f"replay should point at the same QuoteRequest; "
            f"got {second_id} vs first {first_id}"
        )
        assert _count_quote_requests() == baseline + 1, (
            "a replayed form_token must NOT create a second QuoteRequest"
        )
    finally:
        _delete_quote_requests(created_ids)


def test_post_without_form_token_creates_a_quote_request(client, login):
    login("alice@test.local")
    baseline = _count_quote_requests()
    created_ids: set[uuid.UUID] = set()
    try:
        r = client.post(
            "/client/requests/new",
            data=_minimal_wizard_payload(None),
            follow_redirects=False,
        )
        assert r.status_code == 302, (
            f"token-less submit must still create a request; got {r.status_code}"
        )
        new_id = _qr_id_from_redirect(r)
        assert new_id is not None
        created_ids.add(new_id)
        assert _count_quote_requests() == baseline + 1
    finally:
        _delete_quote_requests(created_ids)


def test_malformed_form_token_falls_back_to_legacy_path(client, login):
    login("alice@test.local")
    baseline = _count_quote_requests()
    created_ids: set[uuid.UUID] = set()
    try:
        r = client.post(
            "/client/requests/new",
            data=_minimal_wizard_payload("not-a-uuid"),
            follow_redirects=False,
        )
        assert r.status_code == 302, (
            f"malformed token must fall back, not 500; got {r.status_code}"
        )
        new_id = _qr_id_from_redirect(r)
        assert new_id is not None
        created_ids.add(new_id)
        assert _count_quote_requests() == baseline + 1
    finally:
        _delete_quote_requests(created_ids)


def test_two_distinct_tokens_create_two_quote_requests(client, login):
    login("alice@test.local")
    baseline = _count_quote_requests()
    created_ids: set[uuid.UUID] = set()
    try:
        r1 = client.post(
            "/client/requests/new",
            data=_minimal_wizard_payload(str(uuid.uuid4())),
            follow_redirects=False,
        )
        r2 = client.post(
            "/client/requests/new",
            data=_minimal_wizard_payload(str(uuid.uuid4())),
            follow_redirects=False,
        )
        assert r1.status_code == 302 and r2.status_code == 302
        id1 = _qr_id_from_redirect(r1)
        id2 = _qr_id_from_redirect(r2)
        assert id1 is not None and id2 is not None
        assert id1 != id2, "distinct tokens must materialise as distinct rows"
        created_ids.update({id1, id2})
        assert _count_quote_requests() == baseline + 2, (
            "two distinct tokens must produce two new QuoteRequest rows"
        )
    finally:
        _delete_quote_requests(created_ids)


def test_wizard_get_emits_no_store_header(client, login):
    login("alice@test.local")
    r = client.get("/client/requests/new")
    assert r.status_code == 200, r.data
    assert "no-store" in r.headers.get("Cache-Control", ""), (
        "wizard GET must opt out of bfcache via Cache-Control: no-store"
    )
