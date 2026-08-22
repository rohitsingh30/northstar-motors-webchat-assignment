from webchat.domain.business_semantics import availability_text, money


def test_money_preserves_unknown_and_formats_integer_pence() -> None:
    assert money(None) == "Price on request"
    assert money(2_995_000) == "£29,950.00"


def test_reserved_and_sold_rules_are_explicit() -> None:
    assert "cannot" in availability_text("reserved")
    assert "Sold" in availability_text("sold")
