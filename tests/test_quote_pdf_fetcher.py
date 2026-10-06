"""Le rendu du PDF de devis ne doit jamais sortir sur le réseau (SSRF)."""

import pytest

pytest.importorskip("weasyprint")


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "https://example.com/logo.png",
        "ftp://example.com/x",
        # L'ancien filtre comparait des préfixes sensibles à la casse :
        # ces deux URL passaient au travers.
        "HTTP://169.254.169.254/latest/meta-data/",
        "Https://example.com/logo.png",
    ],
)
def test_pdf_fetcher_refuses_network_urls(url):
    from services.quote_pdf import _safe_fetcher

    with pytest.raises(ValueError, match="disallowed protocol"):
        _safe_fetcher().fetch(url)


def test_pdf_fetcher_still_reads_inline_data():
    from services.quote_pdf import _safe_fetcher

    response = _safe_fetcher().fetch("data:text/plain;charset=utf-8,ok")
    assert response.read() == b"ok"
