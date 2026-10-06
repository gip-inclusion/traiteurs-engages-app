"""Champ libre « Informations complémentaires » du devis.

Saisi dans l'éditeur (section « Informations du devis »), il est le seul
texte libre imprimé sur le document remis au client — `Quote.notes` reste
interne à la fiche. Le document (`_pdf_preview.html`) est partagé par le
PDF et l'aperçu, donc le vérifier sur la page de détail suffit.
"""

import datetime as _dt
from decimal import Decimal

from sqlalchemy import select


def _seed_quote(extra_info=None, status=None):
    from database import session_factory
    from models import (
        Caterer,
        Company,
        QRCStatus,
        Quote,
        QuoteLine,
        QuoteRequest,
        QuoteRequestCaterer,
        QuoteRequestStatus,
        QuoteStatus,
        User,
    )

    s = session_factory()
    try:
        acme = s.scalar(select(Company).where(Company.siret == "12345678901234"))
        caterer = s.scalar(select(Caterer).where(Caterer.siret == "98765432109876"))
        alice = s.scalar(select(User).where(User.email == "alice@test.local"))

        qr = QuoteRequest(
            company_id=acme.id,
            user_id=alice.id,
            guest_count=10,
            status=QuoteRequestStatus.sent_to_caterers,
            event_address="1 rue Test",
            event_city="Paris",
            event_zip_code="75001",
            event_date=_dt.date.today() + _dt.timedelta(days=30),
        )
        s.add(qr)
        s.flush()
        s.add(
            QuoteRequestCaterer(
                quote_request_id=qr.id,
                caterer_id=caterer.id,
                status=QRCStatus.transmitted_to_client,
                response_rank=1,
            )
        )
        quote = Quote(
            quote_request_id=qr.id,
            caterer_id=caterer.id,
            reference=f"DEVIS-EXTRA-{qr.id.hex[:8]}",
            total_amount_ht=Decimal("250"),
            amount_per_person=Decimal("25"),
            notes="Note interne",
            extra_info=extra_info,
            valid_until=_dt.date.today() + _dt.timedelta(days=15),
            status=status or QuoteStatus.sent,
        )
        s.add(quote)
        s.flush()
        s.add(
            QuoteLine(
                quote_id=quote.id,
                position=0,
                section="principal",
                description="Plateau repas",
                quantity=Decimal("10"),
                unit_price_ht=Decimal("25"),
                tva_rate=Decimal("10"),
            )
        )
        s.commit()
        return qr.id, quote.id
    finally:
        s.close()


def test_editor_exposes_the_field(client, login):
    from models import QuoteStatus

    qr_id, q_id = _seed_quote(status=QuoteStatus.draft)
    login("cook@test.local")
    body = client.get(f"/caterer/requests/{qr_id}/quote/{q_id}/edit").data.decode()
    assert 'name="extra_info"' in body
    assert "Informations complémentaires" in body


def test_document_shows_the_field(client, login):
    qr_id, _ = _seed_quote(extra_info="Service en salle inclus jusqu'à 23h")
    login("cook@test.local")
    body = client.get(f"/caterer/requests/{qr_id}").data.decode()
    assert "Service en salle inclus jusqu" in body


def test_document_omits_the_block_when_empty(client, login):
    # Pas de titre orphelin sur le devis quand le traiteur n'a rien saisi.
    qr_id, _ = _seed_quote(extra_info=None)
    login("cook@test.local")
    body = client.get(f"/caterer/requests/{qr_id}").data.decode()
    assert "Informations complémentaires" not in body
