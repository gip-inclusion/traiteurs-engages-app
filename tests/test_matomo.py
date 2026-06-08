"""Intégration Matomo Tag Manager (CSP + injection conditionnelle)."""

import pytest


# ---------------------------------------------------------------------------
# Unitaires : _matomo_origin, _build_csp
# ---------------------------------------------------------------------------


def test_matomo_origin_extracts_scheme_and_host_only():
    from app import _matomo_origin

    assert (
        _matomo_origin("https://matomo.inclusion.beta.gouv.fr/js/container_X.js")
        == "https://matomo.inclusion.beta.gouv.fr"
    )


def test_matomo_origin_empty_returns_empty():
    from app import _matomo_origin

    assert _matomo_origin("") == ""
    assert _matomo_origin(None or "") == ""


def test_matomo_origin_url_without_path_returned_as_is():
    from app import _matomo_origin

    assert _matomo_origin("https://matomo.example.com") == "https://matomo.example.com"


def test_build_csp_without_matomo_keeps_script_src_self_only():
    from app import _build_csp

    csp = _build_csp("")
    assert "script-src 'self';" in csp
    assert "matomo" not in csp


def test_build_csp_with_matomo_adds_origin_to_script_img_connect():
    from app import _build_csp

    url = "https://matomo.inclusion.beta.gouv.fr/js/container_X.js"
    csp = _build_csp(url)
    origin = "https://matomo.inclusion.beta.gouv.fr"
    # script-src doit autoriser l'origine Matomo (pour le container).
    assert f"script-src 'self' {origin};" in csp
    # connect-src pour les requetes de tracking.
    assert origin in csp.split("connect-src", 1)[1].split(";", 1)[0]
    # img-src pour les pixels eventuels.
    assert origin in csp.split("img-src", 1)[1].split(";", 1)[0]


# ---------------------------------------------------------------------------
# Integration : injection conditionnelle dans le HTML et la CSP
# ---------------------------------------------------------------------------


@pytest.fixture
def landing(client):
    return lambda: client.get("/")


def test_matomo_absent_when_setting_empty(landing, monkeypatch):
    import config

    monkeypatch.setattr(config, "MATOMO_TAG_MANAGER_URL", "")
    resp = landing()
    html = resp.get_data(as_text=True)
    assert "matomo-tag-manager.js" not in html
    assert "data-matomo-container-url" not in html
    assert "matomo" not in resp.headers.get("Content-Security-Policy", "").lower()


def test_matomo_injected_when_setting_present(landing, monkeypatch):
    import config

    url = "https://matomo.inclusion.beta.gouv.fr/js/container_p7WStx3R.js"
    monkeypatch.setattr(config, "MATOMO_TAG_MANAGER_URL", url)
    resp = landing()
    html = resp.get_data(as_text=True)
    # Le script bootstrap est inclus, charge depuis self, avec data-attr.
    assert "/static/js/matomo-tag-manager.js" in html
    assert f'data-matomo-container-url="{url}"' in html
    # La CSP a bien ete elargie pour autoriser le domaine Matomo.
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "https://matomo.inclusion.beta.gouv.fr" in csp


def test_matomo_bootstrap_file_is_self_served(client, monkeypatch):
    """Le fichier statique doit etre servi par WhiteNoise sous /static/."""
    resp = client.get("/static/js/matomo-tag-manager.js")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Sanity : on retrouve les balises propres au bootstrap MTM.
    assert "data-matomo-container-url" in body
    assert "_mtm" in body
    assert "mtm.Start" in body
