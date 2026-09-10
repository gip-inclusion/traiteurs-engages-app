"""Parité de rendu : le durcissement des valeurs interpolées doit être
invisible pour le destinataire.

La preuve est exacte, pas visuelle : le durcissement HTML n'ajoute que des
entités numériques (décodables), et le durcissement texte n'ajoute que des
WORD JOINER (supprimables). Si les deux chemins de rendu coïncident après
cette normalisation, ils produisent le même email à l'écran.
"""

import datetime as _dt
import html as _html
from decimal import Decimal
from types import SimpleNamespace

import pytest

from services.email import _WORD_JOINER, _email_envs


_USER = SimpleNamespace(first_name="Élodie", last_name="Durand-Béchu")
_CATERER = SimpleNamespace(name="Traiteur & Cie (ESAT)")
_COMPANY = SimpleNamespace(name="ACME S.A.")

_CTA = "http://localhost:8000/client/requests/42?tab=devis"

_CONTEXTS = {
    "welcome": {"user": _USER, "role_kind": "client", "cta_url": _CTA},
    "password_reset": {
        "user": _USER,
        "reset_url": "http://localhost:8000/reset-password/deadbeef",
        "ttl_minutes": 30,
    },
    "message_received": {
        "user": _USER,
        "sender_name": "Jean-Marc O'Connor",
        "preview": "Bonjour, 50% de remise ? Coût : 12,50 € / pers.",
        "truncated": True,
        "cta_url": _CTA,
    },
    "quote_received": {
        "user": _USER,
        "caterer": _CATERER,
        "event_date": _dt.date(2026, 5, 12),
        "total_amount_ht": Decimal("250.00"),
        "amount_per_person": Decimal("12.50"),
        "valid_until": _dt.date(2026, 5, 19),
        "cta_url": _CTA,
    },
    "order_confirmed": {
        "user": _USER,
        "caterer": _CATERER,
        "company": _COMPANY,
        "quote_reference": "DEVIS-ACME-2026-001",
        "event_date": _dt.date(2026, 5, 12),
        "guest_count": 30,
        "delivery_address": "1 rue de l'Église, 75001 Paris",
        "total_amount_ht": Decimal("450.00"),
        "cta_url": _CTA,
    },
    "quote_request_received": {
        "user": _USER,
        "caterer": _CATERER,
        "company_name": _COMPANY.name,
        "event_date": _dt.date(2026, 5, 12),
        "event_city": "Saint-Étienne",
        "guest_count": 18,
        "cta_url": _CTA,
    },
    "quote_request_updated": {
        "user": _USER,
        "caterer": _CATERER,
        "company_name": _COMPANY.name,
        "event_date": _dt.date(2026, 5, 12),
        "event_city": "Saint-Étienne",
        "guest_count": 18,
        "cta_url": _CTA,
    },
    "quote_request_cancelled": {
        "user": _USER,
        "caterer": _CATERER,
        "company_name": _COMPANY.name,
        "event_date": _dt.date(2026, 5, 12),
        "event_city": "Saint-Étienne",
        "reason": "Événement reporté — budget non validé (cf. mail du 03/04).",
        "cta_url": _CTA,
    },
}


def _render_legacy(app, name, suffix, context):
    """Le chemin d'avant : l'environnement Jinja de l'app, sans durcissement.

    `| trusted` n'y existe pas — on l'y ajoute en identité, de sorte que le
    rendu de référence soit bien celui produit avant le correctif.
    """
    env = app.jinja_env.overlay()
    env.filters = dict(app.jinja_env.filters, trusted=lambda v: v)
    return env.get_template(f"emails/{name}.{suffix}").render(dict(context))


@pytest.mark.parametrize("name", sorted(_CONTEXTS))
def test_html_render_is_visually_identical(app, name):
    context = _CONTEXTS[name]
    with app.app_context():
        html_env, _ = _email_envs(app)
        hardened = html_env.get_template(f"emails/{name}.html").render(dict(context))
        legacy = _render_legacy(app, name, "html", context)

    assert _html.unescape(hardened) == _html.unescape(legacy)


@pytest.mark.parametrize("name", sorted(_CONTEXTS))
def test_text_render_is_visually_identical(app, name):
    context = _CONTEXTS[name]
    with app.app_context():
        _, text_env = _email_envs(app)
        hardened = text_env.get_template(f"emails/{name}.txt").render(dict(context))
        legacy = _render_legacy(app, name, "txt", context)

    assert hardened.replace(_WORD_JOINER, "") == legacy


@pytest.mark.parametrize("name", sorted(_CONTEXTS))
def test_urls_stay_pristine(app, name):
    """`| trusted` doit laisser les URLs intactes : une entité dans un href
    resterait cliquable, mais un WORD JOINER dans la version texte casserait
    le copier-coller."""
    context = _CONTEXTS[name]
    url = context.get("cta_url") or context["reset_url"]
    with app.app_context():
        html_env, text_env = _email_envs(app)
        rendered_html = html_env.get_template(f"emails/{name}.html").render(
            dict(context)
        )
        rendered_text = text_env.get_template(f"emails/{name}.txt").render(
            dict(context)
        )

    # &amp; est l'échappement HTML normal d'un & en attribut : attendu.
    assert url.replace("&", "&amp;") in rendered_html
    assert url in rendered_text


def test_trusted_filter_is_not_exposed_to_web_templates(app):
    """L'opt-out ne doit exister que dans les environnements email."""
    with app.app_context():
        _email_envs(app)
    assert "trusted" not in app.jinja_env.filters
