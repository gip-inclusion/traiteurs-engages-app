# Aggrégations « Impact social » du dashboard client.
#
# Seules les commandes `paid` comptent (achat effectivement réalisé).
# `Quote.total_amount_ht` ne contient pas la commission plateforme : les
# 5% sont sur un `CommissionInvoice` séparé et ne doivent pas entrer
# dans le total impact ; à reconsidérer si un futur refactor inline la
# commission dans Quote.total_amount_ht.
#
# SIAE = EI + ACI ; STPA = ESAT + EA (nomenclature lemarche.inclusion.gouv.fr).
# Ratio heures financées : `round(montant / 26)` reproduit la formule
# publiée par https://lemarche.inclusion.gouv.fr/calculer-impact-social-achat-inclusif/.
from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select

from models import (
    Caterer,
    CatererStructureType,
    Order,
    OrderStatus,
    Quote,
    QuoteRequest,
)

# Exposé comme constante pour qu'un test fige la valeur ; un changement
# silencieux fausserait tous les chiffres.
HOURS_FINANCED_DIVISOR_EUR: int = 26

SIAE_STRUCTURE_TYPES: frozenset[CatererStructureType] = frozenset(
    {CatererStructureType.EI, CatererStructureType.ACI}
)
STPA_STRUCTURE_TYPES: frozenset[CatererStructureType] = frozenset(
    {CatererStructureType.ESAT, CatererStructureType.EA}
)


@dataclass(frozen=True)
class SocialImpact:
    total_ht: Decimal
    siae_ht: Decimal
    stpa_ht: Decimal
    hours_financed: int


def compute_social_impact(
    db,
    *,
    company_id: uuid.UUID,
    requester_user_id: uuid.UUID | None = None,
) -> SocialImpact:
    # requester_user_id reflète le scoping client_admin / client_user pour
    # rester cohérent avec le KPI budget rendu sur la même page.
    # Une seule requête groupée (≤ 4 lignes) au lieu de trois sommes ciblées.
    stmt = (
        select(
            Caterer.structure_type,
            func.coalesce(func.sum(Quote.total_amount_ht), 0),
        )
        .select_from(Order)
        .join(Quote, Order.quote_id == Quote.id)
        .join(QuoteRequest, Quote.quote_request_id == QuoteRequest.id)
        .join(Caterer, Quote.caterer_id == Caterer.id)
        .where(
            Order.status == OrderStatus.paid,
            QuoteRequest.company_id == company_id,
        )
        .group_by(Caterer.structure_type)
    )
    if requester_user_id is not None:
        stmt = stmt.where(QuoteRequest.user_id == requester_user_id)

    total_ht = Decimal(0)
    siae_ht = Decimal(0)
    stpa_ht = Decimal(0)
    for structure_type, amount in db.execute(stmt).all():
        bucket = Decimal(amount or 0)
        total_ht += bucket
        if structure_type in SIAE_STRUCTURE_TYPES:
            siae_ht += bucket
        elif structure_type in STPA_STRUCTURE_TYPES:
            stpa_ht += bucket

    # round() (banker's) plutôt que int() pour rester proche de la valeur
    # affichée par la plateforme officielle.
    hours_financed = (
        int(round(total_ht / HOURS_FINANCED_DIVISOR_EUR)) if total_ht > 0 else 0
    )

    return SocialImpact(
        total_ht=total_ht,
        siae_ht=siae_ht,
        stpa_ht=stpa_ht,
        hours_financed=hours_financed,
    )
