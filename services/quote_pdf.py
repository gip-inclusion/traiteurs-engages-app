from __future__ import annotations

import functools
import os

from flask import current_app, render_template
from weasyprint import CSS, HTML
from weasyprint.urls import default_url_fetcher

from models import MEAL_TYPE_LABELS
from services.quotes import build_pdf_preview


_BLOCKED_SCHEMES = ("http://", "https://", "ftp://", "ftps://")


def _safe_fetch(url):
    if url.startswith(_BLOCKED_SCHEMES):
        raise ValueError(f"PDF render refused network fetch: {url!r}")
    return default_url_fetcher(url)


@functools.cache
def _stylesheets() -> tuple[CSS, ...]:
    css_dir = os.path.join(current_app.static_folder, "css")
    return (
        CSS(filename=os.path.join(css_dir, "tailwind.css")),
        CSS(filename=os.path.join(css_dir, "app.css")),
    )


def render_quote_pdf(quote, qr, caterer) -> bytes:
    pdf_preview = build_pdf_preview(quote, qr, caterer)
    html_str = render_template(
        "caterer/quotes/pdf_document.html",
        quote=quote,
        qr=qr,
        caterer=caterer,
        pdf_preview=pdf_preview,
        meal_type_labels=MEAL_TYPE_LABELS,
    )
    return HTML(string=html_str, url_fetcher=_safe_fetch).write_pdf(
        stylesheets=list(_stylesheets())
    )
