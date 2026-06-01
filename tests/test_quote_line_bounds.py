from decimal import Decimal

import pytest


LEGAL_TVA = {Decimal("0"), Decimal("2.1"), Decimal("5.5"), Decimal("10"), Decimal("20")}


def test_negative_unit_price_is_rejected():
    from services.quotes import lines_from_dicts

    with pytest.raises(ValueError):
        lines_from_dicts(
            [
                {
                    "section": "principal",
                    "description": "x",
                    "quantity": 1,
                    "unit_price_ht": -10,
                    "tva_rate": 10,
                }
            ]
        )


def test_negative_quantity_is_rejected():
    from services.quotes import lines_from_dicts

    with pytest.raises(ValueError):
        lines_from_dicts(
            [
                {
                    "section": "principal",
                    "description": "x",
                    "quantity": -1,
                    "unit_price_ht": 10,
                    "tva_rate": 10,
                }
            ]
        )


def test_illegal_tva_rate_is_rejected():
    from services.quotes import lines_from_dicts

    with pytest.raises(ValueError):
        lines_from_dicts(
            [
                {
                    "section": "principal",
                    "description": "x",
                    "quantity": 1,
                    "unit_price_ht": 10,
                    "tva_rate": 42,
                }
            ]
        )


def test_non_numeric_value_is_rejected():
    from services.quotes import lines_from_dicts

    with pytest.raises(ValueError):
        lines_from_dicts(
            [
                {
                    "section": "principal",
                    "description": "x",
                    "quantity": "NaN",
                    "unit_price_ht": 10,
                    "tva_rate": 10,
                }
            ]
        )


def test_valid_line_still_parses():
    from services.quotes import lines_from_dicts

    lines = lines_from_dicts(
        [
            {
                "section": "principal",
                "description": "plateau",
                "quantity": 20,
                "unit_price_ht": "12.50",
                "tva_rate": "10",
            }
        ]
    )
    assert len(lines) == 1
    assert lines[0].quantity == Decimal("20")
    assert lines[0].unit_price_ht == Decimal("12.50")
    assert lines[0].tva_rate == Decimal("10")


def test_overlong_description_is_rejected():
    from services.quotes import MAX_DESCRIPTION_LEN, lines_from_dicts

    with pytest.raises(ValueError):
        lines_from_dicts(
            [
                {
                    "section": "principal",
                    "description": "x" * (MAX_DESCRIPTION_LEN + 1),
                    "quantity": 1,
                    "unit_price_ht": 10,
                    "tva_rate": 10,
                }
            ]
        )
