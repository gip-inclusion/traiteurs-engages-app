"""Édition admin (super_admin) de l'identité + coordonnées des comptes
traiteur et entreprise (client)."""

from __future__ import annotations

import uuid


def _make_caterer(s):
    from models import Caterer, CatererStructureType

    suffix = uuid.uuid4().hex[:6]
    c = Caterer(
        name=f"Edit-Cat-{suffix}",
        siret=f"77{uuid.uuid4().int % 10**12:012d}"[:14],
        structure_type=CatererStructureType.ESAT,
        invoice_prefix=f"E{suffix[:5]}",
        is_validated=True,
    )
    s.add(c)
    s.flush()
    return c


def _make_company(s, *, siret=None):
    from models import Company

    suffix = uuid.uuid4().hex[:6]
    c = Company(
        name=f"Edit-Co-{suffix}",
        siret=siret or f"33{uuid.uuid4().int % 10**12:012d}"[:14],
    )
    s.add(c)
    s.flush()
    return c


def _cleanup_caterer(caterer_id):
    from database import session_factory
    from models import Caterer

    s = session_factory()
    try:
        s.execute(Caterer.__table__.delete().where(Caterer.id == caterer_id))
        s.commit()
    finally:
        s.close()


def _cleanup_company(company_id):
    from database import session_factory
    from models import Company

    s = session_factory()
    try:
        s.execute(Company.__table__.delete().where(Company.id == company_id))
        s.commit()
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Caterer
# ---------------------------------------------------------------------------


def test_caterer_edit_updates_identity_and_coordinates(client, login):
    from database import session_factory
    from models import Caterer, CatererStructureType

    s = session_factory()
    try:
        cid = _make_caterer(s).id
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/caterers/{cid}/edit",
            data={
                "name": "Nouveau Nom Traiteur",
                "siret": "11111111111111",
                "structure_type": "EA",
                "address": "10 rue Neuve",
                "city": "Lyon",
                "zip_code": "69001",
                "description": "Description mise a jour",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        s2 = session_factory()
        try:
            c = s2.get(Caterer, cid)
            assert c.name == "Nouveau Nom Traiteur"
            assert c.siret == "11111111111111"
            assert c.structure_type == CatererStructureType.EA
            assert c.address == "10 rue Neuve"
            assert c.city == "Lyon"
            assert c.zip_code == "69001"
            assert c.description == "Description mise a jour"
        finally:
            s2.close()
    finally:
        _cleanup_caterer(cid)


def test_caterer_edit_rejects_bad_siret_length(client, login):
    from database import session_factory
    from models import Caterer

    s = session_factory()
    try:
        c = _make_caterer(s)
        cid = c.id
        original_siret = c.siret
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/caterers/{cid}/edit",
            data={"name": "X", "siret": "123", "structure_type": "ESAT"},
            follow_redirects=False,
        )
        assert r.status_code == 400
        s2 = session_factory()
        try:
            assert s2.get(Caterer, cid).siret == original_siret
        finally:
            s2.close()
    finally:
        _cleanup_caterer(cid)


def test_caterer_edit_get_renders_form_with_current_values(client, login):
    from database import session_factory

    s = session_factory()
    try:
        c = _make_caterer(s)
        cid = c.id
        name = c.name
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.get(f"/admin/caterers/{cid}/edit")
        assert r.status_code == 200
        assert name.encode() in r.data
    finally:
        _cleanup_caterer(cid)


def test_caterer_edit_forbidden_for_non_admin(client, login):
    from database import session_factory

    s = session_factory()
    try:
        cid = _make_caterer(s).id
        s.commit()
    finally:
        s.close()
    try:
        login("cook@test.local")  # role caterer
        r = client.get(f"/admin/caterers/{cid}/edit")
        assert r.status_code == 403
    finally:
        _cleanup_caterer(cid)


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------


def test_company_edit_updates_identity_and_coordinates(client, login):
    from database import session_factory
    from models import Company

    s = session_factory()
    try:
        cid = _make_company(s).id
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/companies/{cid}/edit",
            data={
                "name": "Nouvelle Raison Sociale",
                "siret": "22222222222222",
                "address": "5 avenue du Test",
                "city": "Marseille",
                "zip_code": "13001",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        s2 = session_factory()
        try:
            c = s2.get(Company, cid)
            assert c.name == "Nouvelle Raison Sociale"
            assert c.siret == "22222222222222"
            assert c.address == "5 avenue du Test"
            assert c.city == "Marseille"
            assert c.zip_code == "13001"
        finally:
            s2.close()
    finally:
        _cleanup_company(cid)


def test_company_edit_rejects_duplicate_siret(client, login):
    from database import session_factory
    from models import Company

    s = session_factory()
    try:
        existing = _make_company(s, siret="44444444444444")
        existing_id = existing.id
        target = _make_company(s)
        target_id = target.id
        target_original_siret = target.siret
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/companies/{target_id}/edit",
            data={"name": "X", "siret": "44444444444444"},
            follow_redirects=False,
        )
        assert r.status_code == 400
        s2 = session_factory()
        try:
            # Le SIRET cible n'a pas été écrasé par celui déjà pris.
            assert s2.get(Company, target_id).siret == target_original_siret
        finally:
            s2.close()
    finally:
        _cleanup_company(existing_id)
        _cleanup_company(target_id)
