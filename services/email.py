from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request

import dramatiq
from markupsafe import Markup, escape

import config

from services import billing_tasks  # noqa: F401

logger = logging.getLogger(__name__)


_BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"

# Brevo applique son propre moteur de template au contenu brut qu'on lui
# poste (subject / htmlContent / textContent). Toute valeur interpolée dans
# un email est donc durcie au moment du rendu, sans aucune hypothèse sur la
# syntaxe du moteur en aval : Twig ({{ }}, {% %}), merge tags historiques
# Sendinblue (%FIRSTNAME%) ou autre. On neutralise l'ensemble des
# métacaractères ASCII, pas une liste de délimiteurs connus.
# Cf. bugbounty/triage/YWH-PGM43799-3.md.

_WORD_JOINER = "⁠"

_TEMPLATE_OPENER_RE = re.compile(r"\{(?=[{%#])")

_EMAIL_ENVS_KEY = "email_jinja_envs"


class EmailSendError(Exception):
    pass


def _is_inert(char: str) -> bool:
    return char.isalnum() or char.isspace() or not char.isascii()


def _harden_html(value):
    # Un Markup a été marqué sûr explicitement (filtre `trusted`).
    if isinstance(value, Markup):
        return value
    # Les entités numériques *sont* l'échappement HTML : elles couvrent
    # < > & " '. Renvoyer un Markup est indispensable — Jinja applique
    # escape() APRÈS finalize et ré-échapperait sinon le & des entités.
    return Markup("".join(c if _is_inert(c) else f"&#{ord(c)};" for c in str(value)))


def _harden_text(value):
    if isinstance(value, Markup):
        return str(value)
    # Pas d'entités en text/plain : on casse l'adjacence avec un WORD
    # JOINER, invisible et sans effet sur les retours à la ligne.
    return "".join(c if _is_inert(c) else c + _WORD_JOINER for c in str(value))


def _trusted_html(value) -> Markup:
    return escape(value)


def _trusted_text(value) -> Markup:
    return Markup(str(value))


def _email_envs(app):
    envs = app.extensions.get(_EMAIL_ENVS_KEY)
    if envs is None:
        html_env = app.jinja_env.overlay(finalize=_harden_html)
        text_env = app.jinja_env.overlay(finalize=_harden_text)
        # overlay() partage le dict de filtres avec l'environnement web :
        # on le recopie pour ne pas exposer `trusted` aux templates du site.
        html_env.filters = dict(app.jinja_env.filters, trusted=_trusted_html)
        text_env.filters = dict(app.jinja_env.filters, trusted=_trusted_text)
        envs = (html_env, text_env)
        app.extensions[_EMAIL_ENVS_KEY] = envs
    return envs


# Garde-fou de dernier recours, au point de sortie : il couvre un appel
# direct à send_email_sync() qui court-circuiterait les environnements
# durcis. Limité aux accolades — seule famille de délimiteurs dont on peut
# prouver l'absence du markup statique des gabarits (le HTML rendu contient
# légitimement %, <, >, = un peu partout).


def _guard_html(value: str) -> str:
    return _TEMPLATE_OPENER_RE.sub("&#123;", value or "")


def _guard_text(value: str) -> str:
    return _TEMPLATE_OPENER_RE.sub("{" + _WORD_JOINER, value or "")


def _normalise_recipients(to) -> list[dict]:
    if isinstance(to, str):
        return [{"email": to}]
    out: list[dict] = []
    for entry in to:
        if isinstance(entry, str):
            out.append({"email": entry})
        elif isinstance(entry, dict):
            out.append(entry)
        else:
            email, name = entry
            out.append({"email": email, "name": name})
    return out


def _post_to_brevo(payload: dict, api_key: str, *, timeout: float = 10.0) -> None:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _BREVO_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "api-key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                logger.info(
                    "email_sent",
                    extra={
                        "event": "email_sent",
                        "recipient_count": len(payload.get("to", [])),
                        "brevo_status": resp.status,
                    },
                )
                return
            raise EmailSendError(f"unexpected Brevo status {resp.status}")
    except urllib.error.HTTPError as exc:
        body_excerpt = ""
        try:
            body_excerpt = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        if exc.code == 429 or 500 <= exc.code < 600:
            raise EmailSendError(f"Brevo HTTP {exc.code}: {body_excerpt}") from exc
        logger.error(
            "Brevo rejected email (status=%s, body=%s, subject=%s, to=%s)",
            exc.code,
            body_excerpt,
            payload.get("subject"),
            [r.get("email") for r in payload.get("to", [])],
        )
    except (urllib.error.URLError, TimeoutError) as exc:
        raise EmailSendError(f"Brevo network error: {exc}") from exc


def send_email_sync(
    *,
    to,
    subject: str,
    html: str,
    text: str | None = None,
    sender_email: str | None = None,
    sender_name: str | None = None,
    reply_to: str | None = None,
) -> None:
    recipients = _normalise_recipients(to)
    sender = {
        "email": sender_email or config.MAIL_FROM_EMAIL,
        "name": sender_name or config.MAIL_FROM_NAME,
    }
    # _html_to_text doit dériver du HTML brut : neutraliser ensuite,
    # sinon les entités du garde-fou fuient dans la version texte.
    text_content = text or _html_to_text(html)
    payload = {
        "sender": sender,
        "to": recipients,
        "subject": _guard_text(subject),
        "htmlContent": _guard_html(html),
        "textContent": _guard_text(text_content),
    }
    if reply_to:
        payload["replyTo"] = {"email": reply_to}

    api_key = config.BREVO_API_KEY
    if not api_key:
        logger.info(
            "BREVO_API_KEY unset; would have sent email "
            "(subject=%r, to=%s, body_excerpt=%r)",
            payload["subject"],
            [r["email"] for r in recipients],
            payload["textContent"][:500],
        )
        return

    _post_to_brevo(payload, api_key)


def _html_to_text(html: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", no_tags).strip()


@dramatiq.actor(
    max_retries=5,
    min_backoff=30_000,
    max_backoff=8 * 60_000,
    throws=(),
)
def send_email_async(
    *,
    to,
    subject: str,
    html: str,
    text: str | None = None,
    sender_email: str | None = None,
    sender_name: str | None = None,
    reply_to: str | None = None,
) -> None:
    if os.getenv("DRAMATIQ_TESTING") == "1":
        send_email_sync(
            to=to,
            subject=subject,
            html=html,
            text=text,
            sender_email=sender_email,
            sender_name=sender_name,
            reply_to=reply_to,
        )
        return

    send_email_sync(
        to=to,
        subject=subject,
        html=html,
        text=text,
        sender_email=sender_email,
        sender_name=sender_name,
        reply_to=reply_to,
    )


def render_and_send_async(
    *,
    to,
    subject: str,
    template_name: str,
    **context,
) -> None:
    from flask import current_app

    app = current_app._get_current_object()
    html_env, text_env = _email_envs(app)
    app.update_template_context(context)
    html = html_env.get_template(f"emails/{template_name}.html").render(context)
    text = text_env.get_template(f"emails/{template_name}.txt").render(context)
    send_email_async.send(to=to, subject=subject, html=html, text=text)
