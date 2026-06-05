from types import SimpleNamespace

import pytest

from models import MealType
from services.matching import (
    caterer_covers_event_location,
    caterer_offers_meal_type,
    haversine_km,
    is_caterer_eligible,
)


def _caterer(**overrides):
    base = dict(
        service_offerings=None,
        delivery_radius_km=None,
        latitude=None,
        longitude=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _qr(**overrides):
    base = dict(
        meal_type=None,
        event_latitude=None,
        event_longitude=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# haversine_km
# ---------------------------------------------------------------------------


def test_haversine_identity_is_zero():
    assert haversine_km(48.85, 2.35, 48.85, 2.35) == pytest.approx(0.0, abs=1e-6)


def test_haversine_paris_lyon_about_392_km():
    # Paris (48.8566, 2.3522) -> Lyon (45.7640, 4.8357) ~= 392 km
    d = haversine_km(48.8566, 2.3522, 45.7640, 4.8357)
    assert d == pytest.approx(392.0, abs=5.0)


def test_haversine_saclay_bonne_table_proxy():
    # Cas réel rapporté en prod : Mairie de Saclay (~48.731, 2.171) vs
    # Bonne Table à 24 km. On vérifie l'ordre de grandeur entre Saclay et
    # Paris centre (~17-20 km) — confirme que le helper rend la distance
    # attendue dans une zone urbaine.
    d = haversine_km(48.731, 2.171, 48.8566, 2.3522)
    assert 15.0 < d < 25.0


# ---------------------------------------------------------------------------
# caterer_offers_meal_type
# ---------------------------------------------------------------------------


def test_offering_passes_when_qr_meal_type_is_none():
    c = _caterer(service_offerings=["plateaux_repas"])
    qr = _qr(meal_type=None)
    assert caterer_offers_meal_type(c, qr) is True


def test_offering_passes_when_offerings_empty_list():
    c = _caterer(service_offerings=[])
    qr = _qr(meal_type=MealType.cocktail_dejeunatoire)
    assert caterer_offers_meal_type(c, qr) is True


def test_offering_passes_when_offerings_none():
    c = _caterer(service_offerings=None)
    qr = _qr(meal_type=MealType.cocktail_dejeunatoire)
    assert caterer_offers_meal_type(c, qr) is True


def test_offering_matches_when_meal_type_in_offerings():
    c = _caterer(service_offerings=["plateaux_repas", "cocktail_dejeunatoire"])
    qr = _qr(meal_type=MealType.cocktail_dejeunatoire)
    assert caterer_offers_meal_type(c, qr) is True


def test_offering_rejects_when_meal_type_not_in_offerings():
    # Cas Saclay : Bonne Table ne fait que des plateaux repas, demande
    # cocktail déjeunatoire -> exclu.
    c = _caterer(service_offerings=["plateaux_repas"])
    qr = _qr(meal_type=MealType.cocktail_dejeunatoire)
    assert caterer_offers_meal_type(c, qr) is False


# ---------------------------------------------------------------------------
# caterer_covers_event_location
# ---------------------------------------------------------------------------


def test_location_passes_when_no_radius():
    c = _caterer(delivery_radius_km=None, latitude=48.85, longitude=2.35)
    qr = _qr(event_latitude=45.76, event_longitude=4.83)
    assert caterer_covers_event_location(c, qr) is True


def test_location_passes_when_radius_zero():
    c = _caterer(delivery_radius_km=0, latitude=48.85, longitude=2.35)
    qr = _qr(event_latitude=45.76, event_longitude=4.83)
    assert caterer_covers_event_location(c, qr) is True


def test_location_passes_when_caterer_coords_missing():
    c = _caterer(delivery_radius_km=10, latitude=None, longitude=None)
    qr = _qr(event_latitude=48.85, event_longitude=2.35)
    assert caterer_covers_event_location(c, qr) is True


def test_location_passes_when_qr_coords_missing():
    c = _caterer(delivery_radius_km=10, latitude=48.85, longitude=2.35)
    qr = _qr(event_latitude=None, event_longitude=None)
    assert caterer_covers_event_location(c, qr) is True


def test_location_passes_when_distance_within_radius():
    # Paris centre <-> Paris 11e, < 5 km
    c = _caterer(delivery_radius_km=10, latitude=48.8566, longitude=2.3522)
    qr = _qr(event_latitude=48.8580, event_longitude=2.3800)
    assert caterer_covers_event_location(c, qr) is True


def test_location_rejects_when_distance_exceeds_radius():
    # Cas Saclay : Bonne Table rayon 7 km, événement à 24 km -> exclu.
    c = _caterer(delivery_radius_km=7, latitude=48.731, longitude=2.171)
    qr = _qr(event_latitude=48.8566, event_longitude=2.3522)  # Paris centre ~20 km
    assert caterer_covers_event_location(c, qr) is False


# ---------------------------------------------------------------------------
# is_caterer_eligible (combinaison)
# ---------------------------------------------------------------------------


def test_eligible_when_both_rules_pass():
    c = _caterer(
        service_offerings=["cocktail_dejeunatoire"],
        delivery_radius_km=50,
        latitude=48.85,
        longitude=2.35,
    )
    qr = _qr(
        meal_type=MealType.cocktail_dejeunatoire,
        event_latitude=48.86,
        event_longitude=2.36,
    )
    assert is_caterer_eligible(c, qr) is True


def test_ineligible_when_offering_fails():
    c = _caterer(
        service_offerings=["plateaux_repas"],
        delivery_radius_km=50,
        latitude=48.85,
        longitude=2.35,
    )
    qr = _qr(
        meal_type=MealType.cocktail_dejeunatoire,
        event_latitude=48.86,
        event_longitude=2.36,
    )
    assert is_caterer_eligible(c, qr) is False


def test_ineligible_when_distance_fails():
    c = _caterer(
        service_offerings=["cocktail_dejeunatoire"],
        delivery_radius_km=7,
        latitude=48.731,
        longitude=2.171,
    )
    qr = _qr(
        meal_type=MealType.cocktail_dejeunatoire,
        event_latitude=48.8566,
        event_longitude=2.3522,
    )
    assert is_caterer_eligible(c, qr) is False


def test_eligible_when_all_data_missing():
    # Profil traiteur totalement vide + QR vide -> on garde (tolérant).
    c = _caterer()
    qr = _qr()
    assert is_caterer_eligible(c, qr) is True
