import re
import uuid


_REDIRECT_QR_ID = re.compile(r"/client/requests/([0-9a-f-]{36})")


def _qr_id_from_redirect(response) -> uuid.UUID | None:
    location = response.headers.get("Location", "")
    m = _REDIRECT_QR_ID.search(location)
    return uuid.UUID(m.group(1)) if m else None


def _fetch_request(qr_id):
    from database import session_factory
    from models import QuoteRequest

    s = session_factory()
    try:
        return s.get(QuoteRequest, qr_id)
    finally:
        s.close()


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


def test_drinks_selection_is_persisted_on_create(client, login):
    login("alice@test.local")
    created: set[uuid.UUID] = set()
    try:
        r = client.post(
            "/client/requests/new",
            data={
                "drinks_eau_plate": "1",
                "drinks_vins": "1",
                "drinks_champagne": "1",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302, r.data
        new_id = _qr_id_from_redirect(r)
        assert new_id is not None, (
            f"POST must redirect to /client/requests/<id>; got "
            f"Location={r.headers.get('Location')!r}"
        )
        created.add(new_id)

        qr = _fetch_request(new_id)
        assert qr is not None, "POST must have created a QuoteRequest"
        assert set(qr.drinks or []) == {
            "drinks_eau_plate",
            "drinks_vins",
            "drinks_champagne",
        }, f"persisted drinks={qr.drinks!r}"
        assert qr.drinks_alcohol is True, (
            "drinks_alcohol must be True when an alcoholic slug is selected"
        )
    finally:
        _delete_quote_requests(created)


def test_non_alcoholic_only_selection_keeps_drinks_alcohol_false(client, login):
    login("alice@test.local")
    created: set[uuid.UUID] = set()
    try:
        r = client.post(
            "/client/requests/new",
            data={
                "drinks_eau_plate": "1",
                "drinks_soft": "1",
                "drinks_boissons_chaudes": "1",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        new_id = _qr_id_from_redirect(r)
        assert new_id is not None
        created.add(new_id)

        qr = _fetch_request(new_id)
        assert qr is not None
        assert qr.drinks_alcohol is False, (
            "drinks_alcohol must stay False when no alcoholic slug is selected"
        )
    finally:
        _delete_quote_requests(created)


def test_empty_selection_persists_drinks_as_null(client, login):
    login("alice@test.local")
    created: set[uuid.UUID] = set()
    try:
        r = client.post(
            "/client/requests/new",
            data={},
            follow_redirects=False,
        )
        assert r.status_code == 302
        new_id = _qr_id_from_redirect(r)
        assert new_id is not None
        created.add(new_id)

        qr = _fetch_request(new_id)
        assert qr is not None
        assert qr.drinks is None, (
            f"empty selection must store NULL (got {qr.drinks!r}), so "
            "templates can distinguish 'no answer' from 'water-only'"
        )
        assert qr.drinks_alcohol is False
    finally:
        _delete_quote_requests(created)


def test_forged_zero_value_does_not_count_as_ticked(client, login):
    login("alice@test.local")
    created: set[uuid.UUID] = set()
    try:
        r = client.post(
            "/client/requests/new",
            data={"drinks_vins": "0"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        new_id = _qr_id_from_redirect(r)
        assert new_id is not None
        created.add(new_id)

        qr = _fetch_request(new_id)
        assert qr is not None
        assert (qr.drinks or []) == [], (
            f"drinks_vins=0 must not be treated as ticked (got {qr.drinks!r})"
        )
        assert qr.drinks_alcohol is False
    finally:
        _delete_quote_requests(created)


def test_edit_page_pre_ticks_the_saved_selection(client, login):
    login("alice@test.local")
    created: set[uuid.UUID] = set()
    try:
        # `is_compare_mode=on` parks the QR in `pending_review` rather
        # than `sent_to_caterers`, which is the only status that lets
        # the client re-open the wizard via /edit. Without it the route
        # 302s back to the read-only detail and the assertion below fails.
        r = client.post(
            "/client/requests/new",
            data={
                "drinks_eau_plate": "1",
                "drinks_bieres": "1",
                "is_compare_mode": "on",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        new_id = _qr_id_from_redirect(r)
        assert new_id is not None
        created.add(new_id)

        r = client.get(f"/client/requests/{new_id}/edit")
        assert r.status_code == 200, r.data
        html = r.data.decode("utf-8", errors="replace")
        # Both saved slugs must come back rendered as `checked` in the
        # form so the user can iterate.
        assert 'name="drinks_eau_plate"' in html and "checked" in html, (
            "edit page must pre-tick a previously-saved soft drink"
        )
        assert 'name="drinks_bieres"' in html, (
            "edit page must surface the alcohol checkbox too"
        )
    finally:
        _delete_quote_requests(created)
