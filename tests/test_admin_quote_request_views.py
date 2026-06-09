"""Tests des vues admin pour distinguer demandes directes / 3 devis et
afficher le compteur « demandes en attente » par traiteur.

Deux changements UI couverts :

* `/admin/requests` affiche, sous le type, un badge « 3 devis » (mise
  en concurrence) ou « Direct → {nom du traiteur} » (envoi direct).
* `/admin/caterers` affiche, à côté du nom de chaque traiteur, un badge
  « X demande(s) en attente » quand au moins un `QuoteRequestCaterer`
  est `selected` sur une `QuoteRequest` encore active
  (`sent_to_caterers`). Les demandes mortes (cancelled / completed /
  quotes_refused) ne gonflent pas le compteur.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from decimal import Decimal

from sqlalchemy import select


def _seed_qr(s, *, is_compare_mode=True, status=None, target_caterer=None):
    """Crée une `QuoteRequest` pour ACME + alice et, si `target_caterer`
    est fourni, lui rattache un `QuoteRequestCaterer.selected`."""
    from models import (
        Company,
        MealType,
        QRCStatus,
        QuoteRequest,
        QuoteRequestCaterer,
        QuoteRequestStatus,
        User,
    )

    acme = s.scalar(select(Company).where(Company.siret == "12345678901234"))
    alice = s.scalar(select(User).where(User.email == "alice@test.local"))
    qr = QuoteRequest(
        company_id=acme.id,
        user_id=alice.id,
        meal_type=MealType.plateaux_repas,
        event_date=_dt.date.today() + _dt.timedelta(days=30),
        guest_count=12,
        status=status or QuoteRequestStatus.sent_to_caterers,
        is_compare_mode=is_compare_mode,
    )
    s.add(qr)
    s.flush()
    if target_caterer is not None:
        s.add(
            QuoteRequestCaterer(
                quote_request_id=qr.id,
                caterer_id=target_caterer.id,
                status=QRCStatus.selected,
            )
        )
        s.flush()
    return qr.id


def _make_throwaway_caterer(s):
    from models import Caterer, CatererStructureType

    suffix = uuid.uuid4().hex[:6]
    c = Caterer(
        name=f"Throw-{suffix}",
        siret=f"66{uuid.uuid4().int % 10**12:012d}"[:14],
        structure_type=CatererStructureType.ESAT,
        invoice_prefix=f"T{suffix[:5]}",
        is_validated=True,
        commission_rate=Decimal("0.05"),
    )
    s.add(c)
    s.flush()
    return c


def _add_caterer_user(s, caterer):
    """Attache un user actif au traiteur (destinataire des messages)."""
    from models import User, UserRole

    u = User(
        email=f"cat-{uuid.uuid4().hex[:6]}@test.local",
        password_hash="x",
        first_name="Cat",
        last_name="X",
        role=UserRole.caterer,
        caterer_id=caterer.id,
    )
    s.add(u)
    s.flush()
    return u


def _cleanup_users(user_ids):
    from database import session_factory
    from models import User

    s = session_factory()
    try:
        for uid in user_ids:
            s.execute(User.__table__.delete().where(User.id == uid))
        s.commit()
    finally:
        s.close()


def _cleanup_qrs(qr_ids):
    from database import session_factory
    from models import QuoteRequest, QuoteRequestCaterer

    s = session_factory()
    try:
        for qid in qr_ids:
            s.execute(
                QuoteRequestCaterer.__table__.delete().where(
                    QuoteRequestCaterer.quote_request_id == qid
                )
            )
            s.execute(QuoteRequest.__table__.delete().where(QuoteRequest.id == qid))
        s.commit()
    finally:
        s.close()


def _cleanup_caterer(caterer_id):
    from database import session_factory
    from models import Caterer

    s = session_factory()
    try:
        s.execute(Caterer.__table__.delete().where(Caterer.id == caterer_id))
        s.commit()
    finally:
        s.close()


# ---------------------------------------------------------------------------
# /admin/requests : badge 3 devis / Direct
# ---------------------------------------------------------------------------


def test_requests_list_shows_3_devis_badge_for_compare_mode(client, login):
    from database import session_factory

    s = session_factory()
    try:
        qr_id = _seed_qr(s, is_compare_mode=True)
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.get("/admin/requests")
        assert r.status_code == 200
        assert b"3 devis" in r.data
    finally:
        _cleanup_qrs([qr_id])


def test_requests_list_shows_direct_badge_with_caterer_name(client, login):
    from database import session_factory

    s = session_factory()
    try:
        target = _make_throwaway_caterer(s)
        target_name = target.name
        target_id = target.id
        qr_id = _seed_qr(s, is_compare_mode=False, target_caterer=target)
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.get("/admin/requests")
        assert r.status_code == 200
        assert b"Direct" in r.data
        assert target_name.encode() in r.data
    finally:
        _cleanup_qrs([qr_id])
        _cleanup_caterer(target_id)


# ---------------------------------------------------------------------------
# /admin/caterers : compteur « X en attente »
# ---------------------------------------------------------------------------


def test_caterers_list_shows_pending_counter_for_selected_qrc(client, login):
    from database import session_factory

    s = session_factory()
    try:
        caterer = _make_throwaway_caterer(s)
        caterer_id = caterer.id
        caterer_name = caterer.name
        # 2 demandes actives ciblées sur ce traiteur, QRC selected.
        qr1 = _seed_qr(s, is_compare_mode=False, target_caterer=caterer)
        qr2 = _seed_qr(s, is_compare_mode=False, target_caterer=caterer)
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.get("/admin/caterers")
        assert r.status_code == 200
        # Le compteur doit être de 2 pour ce traiteur. On vérifie que la
        # chaîne « 2 demandes en attente » apparaît dans la page (le badge
        # est plein-texte, simple à matcher).
        assert b"2 demandes en attente" in r.data
        assert caterer_name.encode() in r.data
    finally:
        _cleanup_qrs([qr1, qr2])
        _cleanup_caterer(caterer_id)


# ---------------------------------------------------------------------------
# /admin/qualification/<id> : bloc « Traiteur » pour les demandes directes
# ---------------------------------------------------------------------------


def test_qualification_detail_no_caterer_block_for_compare_mode(client, login):
    """En mode 3 devis, pas de traiteur cible unique : le bloc Traiteur
    ne doit pas apparaître."""
    from database import session_factory

    s = session_factory()
    try:
        qr_id = _seed_qr(s, is_compare_mode=True)
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.get(f"/admin/qualification/{qr_id}")
        assert r.status_code == 200
        assert b"Voir la fiche traiteur" not in r.data
    finally:
        _cleanup_qrs([qr_id])


def test_qualification_detail_shows_caterer_block_for_direct(client, login):
    """Demande directe : bloc Traiteur avec nom, lien fiche traiteur et
    bouton message (le traiteur a un user actif)."""
    from database import session_factory

    s = session_factory()
    try:
        target = _make_throwaway_caterer(s)
        target_name = target.name
        target_id = target.id
        cat_user = _add_caterer_user(s, target)
        cat_user_id = cat_user.id
        qr_id = _seed_qr(s, is_compare_mode=False, target_caterer=target)
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.get(f"/admin/qualification/{qr_id}")
        assert r.status_code == 200
        body = r.data
        # Bloc Traiteur + nom du traiteur
        assert target_name.encode() in body
        # Lien vers la fiche traiteur admin
        assert f"/admin/caterers/{target_id}".encode() in body
        assert b"Voir la fiche traiteur" in body
        # Bouton message (un user existe)
        assert b"Envoyer un message au traiteur" in body
    finally:
        _cleanup_qrs([qr_id])
        _cleanup_users([cat_user_id])
        _cleanup_caterer(target_id)


def test_qualification_detail_dispatched_has_no_actions_block(client, login):
    """Demande déjà envoyée : pas de bloc Actions (Valider/Rejeter), mais
    le bouton message au client reste (déplacé dans le bloc Entreprise)."""
    from database import session_factory

    s = session_factory()
    try:
        target = _make_throwaway_caterer(s)
        target_id = target.id
        qr_id = _seed_qr(s, is_compare_mode=False, target_caterer=target)
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.get(f"/admin/qualification/{qr_id}")
        assert r.status_code == 200
        body = r.data
        assert b"Valider la demande" not in body
        assert b"Rejeter" not in body
        assert b"Envoyer un message au client" in body
    finally:
        _cleanup_qrs([qr_id])
        _cleanup_caterer(target_id)


def test_qualification_detail_pending_keeps_approve_reject(client, login):
    """Demande en attente : bloc Actions conservé (Valider + Rejeter), et
    le bouton message au client reste présent (dans le bloc Entreprise)."""
    from database import session_factory
    from models import QuoteRequestStatus

    s = session_factory()
    try:
        qr_id = _seed_qr(
            s, is_compare_mode=True, status=QuoteRequestStatus.pending_review
        )
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.get(f"/admin/qualification/{qr_id}")
        assert r.status_code == 200
        body = r.data
        assert b"Valider la demande" in body
        assert b"Rejeter" in body
        assert b"Envoyer un message au client" in body
    finally:
        _cleanup_qrs([qr_id])


def test_caterers_list_excludes_dead_quote_requests_from_counter(client, login):
    """Une QRC `selected` dont la QR parente est `cancelled` ne doit pas
    apparaître dans le compteur (la demande est morte)."""
    from database import session_factory
    from models import QuoteRequestStatus

    s = session_factory()
    try:
        caterer = _make_throwaway_caterer(s)
        caterer_id = caterer.id
        caterer_name = caterer.name
        qr = _seed_qr(
            s,
            is_compare_mode=False,
            target_caterer=caterer,
            status=QuoteRequestStatus.cancelled,
        )
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.get("/admin/caterers")
        assert r.status_code == 200
        # Le nom du traiteur apparaît mais sans badge "en attente".
        assert caterer_name.encode() in r.data
        # La phrase exacte du badge ne doit pas être présente pour ce
        # traiteur. Sur une DB de test fraîche, aucun autre traiteur n'a
        # de QRC en attente non plus.
        assert b"demande en attente" not in r.data
        assert b"demandes en attente" not in r.data
    finally:
        _cleanup_qrs([qr])
        _cleanup_caterer(caterer_id)
