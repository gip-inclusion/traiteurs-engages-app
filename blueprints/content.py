"""Pages de contenu SEO (articles).

Pages publiques optimisées pour le référencement, accessibles uniquement
depuis le footer de la landing. Pour publier un nouvel article : ajouter
une entrée à ``ARTICLES`` et son template ``templates/content/<...>.html``
(qui étend ``content/_base.html``).
"""

from dataclasses import dataclass

from flask import Blueprint, render_template

import config

content_bp = Blueprint("content", __name__)


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
    "traiteur-handicap-entreprise-paris": Article(
        slug="traiteur-handicap-entreprise-paris",
        template="content/traiteur_handicap_entreprise_paris.html",
        title="Traiteur handicap Paris — Entreprises adaptées & insertion",
        description=(
            "Faites appel à un traiteur employant des personnes handicapées "
            "pour vos événements en IDF. Structures ESAT et EA vérifiées. "
            "Plateforme officielle inclusion.gouv.fr."
        ),
        breadcrumb="Traiteur handicap à Paris",
    ),
    "traiteur-seminaire-entreprise-paris": Article(
        slug="traiteur-seminaire-entreprise-paris",
        template="content/traiteur_seminaire_entreprise_paris.html",
        title="Traiteur séminaire Paris — Prestataire engagé & solidaire",
        description=(
            "Trouvez un traiteur pour votre séminaire à Paris : structures "
            "solidaires et entreprises adaptées en IDF. Buffet, déjeuner, "
            "pauses. Plateforme inclusion.gouv.fr."
        ),
        breadcrumb="Traiteur séminaire à Paris",
    ),
}


@content_bp.app_context_processor
def inject_articles():
    """Expose les articles SEO à tous les templates (liens de footer)."""
    return {"seo_articles": list(ARTICLES.values())}


# URLs en racine (`/<slug>`) restreintes aux slugs publiés via le convertisseur
# `any` : seules les vraies pages d'articles matchent, aucune autre route de
# l'app n'est captée (un slug inconnu → 404 Flask standard). La règle se met à
# jour automatiquement quand on ajoute un article au registre.
_SLUG_RULE = "/<any({}):slug>".format(",".join(f"'{s}'" for s in ARTICLES))


@content_bp.route(_SLUG_RULE)
def article(slug: str):
    art = ARTICLES[slug]
    canonical_url = f"{config.BASE_URL}/{art.slug}"
    return render_template(
        art.template,
        article=art,
        canonical_url=canonical_url,
        base_url=config.BASE_URL,
    )
