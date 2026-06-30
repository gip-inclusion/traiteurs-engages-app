"""Pages de contenu SEO (articles)."""

from __future__ import annotations


def test_article_renders_with_seo_metadata(client):
    r = client.get("/ressources/traiteur-solidaire-paris")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    # H1 et chapeau
    assert "Traiteur solidaire Paris : des événements d'entreprise" in body
    assert 'class="article-lede"' in body
    # Balises meta SEO
    assert '<meta name="description"' in body
    # apostrophe échappée par l'autoescape Jinja (&#39;) → on teste un fragment sans apostrophe
    assert "entreprises adaptées pour vos événements pro" in body
    assert '<link rel="canonical"' in body
    assert "/ressources/traiteur-solidaire-paris" in body
    # Open Graph
    assert 'property="og:title"' in body
    # Données structurées
    assert "application/ld+json" in body
    assert '"@type": "FAQPage"' in body
    assert '"@type": "BreadcrumbList"' in body
    # CTA vers le catalogue
    assert 'class="article-cta-btn"' in body
    assert "/search" in body or "catalogue" in body.lower()
    # En-tête + pied de page publics partagés, avec lien Ressources vers l'article
    assert 'class="public-header"' in body
    assert 'class="public-footer"' in body
    assert "Ressources" in body
    assert body.count("/ressources/traiteur-solidaire-paris") >= 2  # canonical + footer


def test_unknown_article_is_404(client):
    assert client.get("/ressources/n-existe-pas").status_code == 404


def test_landing_footer_links_to_article(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert "/ressources/traiteur-solidaire-paris" in body
    assert "Ressources" in body
