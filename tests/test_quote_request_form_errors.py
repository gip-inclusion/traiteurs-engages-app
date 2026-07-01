"""Remontée des erreurs de validation sur la demande de devis client.

Avant : échec de validation → flash générique + wizard vidé, impossible de
savoir quel champ pose problème (ni pour le client ni dans les logs).
Après : message ciblé (dont un cas dédié CSRF) + `form.errors` journalisé.
"""

from __future__ import annotations


def test_new_request_invalid_field_flashes_specific_message(client, login):
    login("alice@test.local")
    r = client.post(
        "/client/requests/new",
        data={"guest_count": "0", "meal_type": "aperitif"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    body = r.data.decode("utf-8")
    # champ fautif nommé en clair, plus de message opaque seul
    assert "Nombre de convives" in body
    assert "Merci de vérifier" in body


def test_flash_helper_csrf_gives_reload_message(app):
    from flask import get_flashed_messages

    from blueprints.client.requests import _flash_quote_form_errors

    class _FakeForm:
        errors = {"csrf_token": ["The CSRF token is invalid."]}

    with app.test_request_context():
        _flash_quote_form_errors(_FakeForm())
        messages = get_flashed_messages()

    assert any("session a expiré" in m.lower() for m in messages)
    assert not any("Merci de vérifier" in m for m in messages)
