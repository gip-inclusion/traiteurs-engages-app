from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

import dramatiq

import config

from services import billing_tasks  # noqa: F401

logger = logging.getLogger(__name__)


_BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"


class EmailSendError(Exception):
    pass


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
    payload = {
        "sender": sender,
        "to": recipients,
        "subject": subject,
        "htmlContent": html,
        "textContent": text or _html_to_text(html),
    }
    if reply_to:
        payload["replyTo"] = {"email": reply_to}

    api_key = config.BREVO_API_KEY
    if not api_key:
        logger.info(
            "BREVO_API_KEY unset; would have sent email "
            "(subject=%r, to=%s, body_excerpt=%r)",
            subject,
            [r["email"] for r in recipients],
            (text or _html_to_text(html))[:500],
        )
        return

    _post_to_brevo(payload, api_key)


def _html_to_text(html: str) -> str:
    import re

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
    from flask import render_template

    html = render_template(f"emails/{template_name}.html", **context)
    text = render_template(f"emails/{template_name}.txt", **context)
    send_email_async.send(to=to, subject=subject, html=html, text=text)
