from decimal import Decimal

from app.pricing import compute_cost, price_for_model


def test_compute_cost_known_model() -> None:
    assert compute_cost("gpt-4o", 1_000_000, 500_000) == Decimal("7.500000")


def test_compute_cost_unknown_model_is_free() -> None:
    assert compute_cost("fake-model-0", 1_000_000, 1_000_000) == Decimal("0.000000")


def test_compute_cost_rounds_to_six_places() -> None:
    assert compute_cost("gpt-4o", 1, 1) == Decimal("0.000012")


def test_price_for_model_unknown_falls_back_to_default() -> None:
    assert price_for_model("nope") == (Decimal("0"), Decimal("0"))
