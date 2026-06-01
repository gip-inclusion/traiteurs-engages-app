from decimal import Decimal


def test_split_invoice_amounts_matches_stripe_transfer():
    from services.stripe_service import split_invoice_amounts

    total_ttc = Decimal("120.00")
    fee_ht = Decimal("5.00")
    fee_tva = Decimal("1.00")

    result = split_invoice_amounts(
        total_ttc=total_ttc,
        fee_ht=fee_ht,
        fee_tva=fee_tva,
    )

    fee_ttc_cents = 500 + 100
    invoice_total_cents = 12000 + fee_ttc_cents
    assert result.invoice_total_cents == invoice_total_cents
    assert result.application_fee_cents == fee_ttc_cents
    assert result.amount_to_caterer_cents == 12000, (
        f"caterer should receive {12000} cents, got {result.amount_to_caterer_cents}"
    )
    assert (
        result.amount_to_caterer_cents + result.application_fee_cents
        == result.invoice_total_cents
    )
