"""Pages de contenu SEO (articles)."""

from __future__ import annotations


def test_article_renders_with_seo_metadata(client):
    r = client.get("/traiteur-solidaire-paris")
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
    assert "/traiteur-solidaire-paris" in body
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
    assert body.count('href="/traiteur-solidaire-paris"') >= 1  # footer


def test_handicap_article_renders(client):
    r = client.get("/traiteur-handicap-entreprise-paris")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert "handicap Paris : organisez vos événements" in body
    assert "entreprises adaptées (EA) et ESAT" in body
    assert '"@type": "FAQPage"' in body
    assert "AGEFIPH" in body
    # cross-link : les deux articles sont listés dans le footer partout
    assert 'href="/traiteur-solidaire-paris"' in body
    assert 'href="/traiteur-handicap-entreprise-paris"' in body


def test_seminaire_article_renders(client):
    r = client.get("/traiteur-seminaire-entreprise-paris")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert "Traiteur pour un séminaire à Paris : bien manger, bien faire" in body
    assert '"@type": "FAQPage"' in body
    assert "Déjeuner buffet" in body
    # cross-link : les trois articles listés dans le footer partout
    assert 'href="/traiteur-solidaire-paris"' in body
    assert 'href="/traiteur-handicap-entreprise-paris"' in body
    assert 'href="/traiteur-seminaire-entreprise-paris"' in body


def test_cocktail_article_renders(client):
    r = client.get("/traiteur-cocktail-soiree-entreprise-paris")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert "Traiteur cocktail entreprise Paris" in body
    assert '"@type": "FAQPage"' in body
    assert "cocktail dinatoire" in body.lower()
    assert 'href="/traiteur-cocktail-soiree-entreprise-paris"' in body


def test_unknown_article_is_404(client):
    assert client.get("/n-existe-pas-du-tout").status_code == 404


def test_landing_footer_links_to_all_articles(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert 'href="/traiteur-solidaire-paris"' in body
    assert 'href="/traiteur-handicap-entreprise-paris"' in body
    assert 'href="/traiteur-seminaire-entreprise-paris"' in body
    assert 'href="/traiteur-cocktail-soiree-entreprise-paris"' in body
    assert "Ressources" in body
