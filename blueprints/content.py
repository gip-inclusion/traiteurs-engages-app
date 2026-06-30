"""Pages de contenu SEO (articles).

Pages publiques optimisées pour le référencement, accessibles uniquement
depuis le footer de la landing. Pour publier un nouvel article : ajouter
une entrée à ``ARTICLES`` et son template ``templates/content/<...>.html``
(qui étend ``content/_base.html``).
"""

from dataclasses import dataclass

from flask import Blueprint, abort, render_template

import config

content_bp = Blueprint("content", __name__, url_prefix="/ressources")


@dataclass(frozen=True)
class Article:
    slug: str
    template: str
    title: str  # balise <title> + og:title
    description: str  # meta description + og:description
    breadcrumb: str  # libellé court (fil d'Ariane)


# Articles SEO publiés. L'ordre est celui d'affichage dans le footer.
ARTICLES: dict[str, "Article"] = {
    "traiteur-solidaire-paris": Article(
        slug="traiteur-solidaire-paris",
        template="content/traiteur_solidaire_paris.html",
        title="Traiteur solidaire Paris — Trouvez votre prestataire engagé",
        description=(
            "Découvrez nos traiteurs solidaires en Île-de-France : structures "
            "d'insertion et entreprises adaptées pour vos événements pro. "
            "Plateforme officielle inclusion.gouv.fr"
        ),
        breadcrumb="Traiteur solidaire à Paris",
    ),
}


@content_bp.app_context_processor
def inject_articles():
    """Expose les articles SEO à tous les templates (liens de footer)."""
    return {"seo_articles": list(ARTICLES.values())}


@content_bp.route("/<slug>")
def article(slug: str):
    art = ARTICLES.get(slug)
    if art is None:
        abort(404)
    canonical_url = f"{config.BASE_URL}/ressources/{art.slug}"
    return render_template(
        art.template,
        article=art,
        canonical_url=canonical_url,
        base_url=config.BASE_URL,
    )
