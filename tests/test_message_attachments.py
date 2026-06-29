"""Tests des pièces jointes dans la messagerie.

Couvre l'envoi (multipart) avec fichier, la PJ seule (sans texte), le
rejet d'un type interdit, et surtout le **contrôle d'accès** à la route
de téléchargement : seuls les participants du thread peuvent récupérer
la PJ (un thread est privé).

L'envoi se teste en tant que super_admin → user : ça court-circuite le
gate VULN-04 (relation métier) sans avoir à monter une commande/devis.
CSRF est désactivé en test (cf. conftest).
"""

import io
import uuid

from PIL import Image
from sqlalchemy import select


def _png_upload():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buf, format="PNG")
    buf.seek(0)
    return buf


def _user_id(email):
    from database import session_factory
    from models import User

    s = session_factory()
    try:
        return s.scalar(select(User.id).where(User.email == email))
    finally:
        s.close()


def _latest_message_with_attachment(thread_id):
    from database import session_factory
    from models import Message

    s = session_factory()
    try:
        return s.scalar(
            select(Message)
            .where(Message.thread_id == thread_id)
            .where(Message.attachment_url.is_not(None))
            .order_by(Message.created_at.desc())
        )
    finally:
        s.close()


def _cleanup_thread(thread_id):
    from database import session_factory
    from models import Message

    s = session_factory()
    try:
        s.execute(Message.__table__.delete().where(Message.thread_id == thread_id))
        s.commit()
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Envoi
# ---------------------------------------------------------------------------


def test_send_message_with_attachment(client, login):
    login("admin@test.local")  # super_admin → skip VULN-04 gate
    alice_id = _user_id("alice@test.local")
    r = client.post(
        "/api/messages",
        data={
            "recipient_id": str(alice_id),
            "body": "Voici le document",
            "file": (_png_upload(), "doc.png"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 201, r.data
    thread_id = r.get_json()["thread_id"]
    try:
        msg = _latest_message_with_attachment(uuid.UUID(thread_id))
        assert msg is not None
        assert msg.attachment_url
        assert msg.attachment_name == "doc.png"
    finally:
        _cleanup_thread(uuid.UUID(thread_id))


def test_send_attachment_only_without_body(client, login):
    """PJ seule autorisée : body vide + fichier → 201."""
    login("admin@test.local")
    alice_id = _user_id("alice@test.local")
    r = client.post(
        "/api/messages",
        data={
            "recipient_id": str(alice_id),
            "body": "",
            "file": (_png_upload(), "photo.png"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 201, r.data
    thread_id = r.get_json()["thread_id"]
    try:
        msg = _latest_message_with_attachment(uuid.UUID(thread_id))
        assert msg is not None
        assert msg.body == ""
        assert msg.attachment_url
    finally:
        _cleanup_thread(uuid.UUID(thread_id))


def test_send_message_without_body_or_file_is_rejected(client, login):
    login("admin@test.local")
    alice_id = _user_id("alice@test.local")
    r = client.post(
        "/api/messages",
        json={"recipient_id": str(alice_id), "body": ""},
    )
    assert r.status_code == 400


def test_send_message_rejects_disallowed_file_type(client, login):
    login("admin@test.local")
    alice_id = _user_id("alice@test.local")
    r = client.post(
        "/api/messages",
        data={
            "recipient_id": str(alice_id),
            "body": "",
            "file": (io.BytesIO(b"hello world"), "notes.txt"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Contrôle d'accès à la pièce jointe (le point sécurité)
# ---------------------------------------------------------------------------


def _send_with_attachment(client, login):
    """Envoie une PJ (admin → alice) et renvoie (message_id, thread_id)."""
    login("admin@test.local")
    alice_id = _user_id("alice@test.local")
    r = client.post(
        "/api/messages",
        data={
            "recipient_id": str(alice_id),
            "body": "doc",
            "file": (_png_upload(), "secret.png"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 201, r.data
    thread_id = uuid.UUID(r.get_json()["thread_id"])
    msg = _latest_message_with_attachment(thread_id)
    return msg.id, thread_id


def test_attachment_downloadable_by_sender(client, login):
    msg_id, thread_id = _send_with_attachment(client, login)
    try:
        # admin est le sender (toujours loggé)
        r = client.get(f"/api/messages/{msg_id}/attachment")
        assert r.status_code == 200
        assert r.data[:8] == b"\x89PNG\r\n\x1a\n"  # vrai PNG ré-encodé
    finally:
        _cleanup_thread(thread_id)


def test_attachment_downloadable_by_recipient(client, login):
    msg_id, thread_id = _send_with_attachment(client, login)
    try:
        login("alice@test.local")  # recipient
        r = client.get(f"/api/messages/{msg_id}/attachment")
        assert r.status_code == 200
    finally:
        _cleanup_thread(thread_id)


def test_attachment_forbidden_for_third_party(client, login):
    """bob n'est ni sender ni recipient → 403, la PJ ne fuite pas."""
    msg_id, thread_id = _send_with_attachment(client, login)
    try:
        login("bob@test.local")  # tiers
        r = client.get(f"/api/messages/{msg_id}/attachment")
        assert r.status_code == 403
    finally:
        _cleanup_thread(thread_id)


def test_attachment_404_for_unknown_message(client, login):
    login("admin@test.local")
    r = client.get(f"/api/messages/{uuid.uuid4()}/attachment")
    assert r.status_code == 404
