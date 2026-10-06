from __future__ import annotations

import functools
import os

from flask import current_app, render_template
from weasyprint import CSS, HTML
from weasyprint.urls import URLFetcher

from models import MEAL_TYPE_LABELS
from services.quotes import build_pdf_preview


# The PDF never needs the network: an allowlist, rather than the former
# blocklist of http/https/ftp prefixes, which was case-sensitive and let
# "HTTP://..." through. WeasyPrint lowercases the scheme before checking it
# and refuses anything else with a ValueError, so the resource is skipped.
_ALLOWED_PROTOCOLS = frozenset({"data", "file"})


def _safe_fetcher() -> URLFetcher:
    return URLFetcher(allowed_protocols=_ALLOWED_PROTOCOLS, allow_redirects=False)


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
    return HTML(string=html_str, url_fetcher=_safe_fetcher()).write_pdf(
        stylesheets=list(_stylesheets())
    )
