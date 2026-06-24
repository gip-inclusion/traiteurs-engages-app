"""Règles d'éligibilité d'un traiteur pour une demande de devis.

Politique tolérante : on n'exclut un traiteur que si le profil contient
suffisamment d'informations pour conclure qu'il n'est pas pertinent. Les
données manquantes (offerings vides, rayon non renseigné, coordonnées
absentes) laissent le traiteur dans le fan-out — pour ne pas pénaliser
les profils incomplets le temps qu'ils soient enrichis.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import Caterer, QuoteRequest


_EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance grand-cercle entre deux points (km)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_KM * c


def caterer_offers_meal_type(caterer: "Caterer", quote_request: "QuoteRequest") -> bool:
    """True si le traiteur propose le type de prestation demandé.

    Tolérant : si la demande n'a pas de meal_type, ou si le traiteur n'a
    pas renseigné ses offerings, on garde le traiteur.
    """
    meal_type = quote_request.meal_type
    if meal_type is None:
        return True
    offerings = caterer.service_offerings or []
    if not offerings:
        return True
    # meal_type est une MealType en mémoire (objet Python) mais revient
    # comme str après round-trip DB (la colonne est String(40), pas un
    # Enum SQLA). On normalise.
    target = getattr(meal_type, "value", meal_type)
    return target in offerings


def caterer_covers_event_location(
    caterer: "Caterer", quote_request: "QuoteRequest"
) -> bool:
    """True si l'événement est dans le rayon d'intervention du traiteur.

    Tolérant : rayon None/0, coordonnées traiteur manquantes ou
    coordonnées événement manquantes → on garde le traiteur.
    """
    radius = caterer.delivery_radius_km
    if not radius:
        return True
    if caterer.latitude is None or caterer.longitude is None:
        return True
    if quote_request.event_latitude is None or quote_request.event_longitude is None:
        return True
    distance = haversine_km(
        caterer.latitude,
        caterer.longitude,
        quote_request.event_latitude,
        quote_request.event_longitude,
    )
    return distance <= radius


def is_caterer_eligible(caterer: "Caterer", quote_request: "QuoteRequest") -> bool:
    """Combinaison des deux règles : offering ET distance."""
    return caterer_offers_meal_type(
        caterer, quote_request
    ) and caterer_covers_event_location(caterer, quote_request)
