"""Per-model pricing used to cost each run item.

Prices are USD per 1M tokens, keyed by provider model id. Unknown models
(and fake/local endpoints) fall back to $0, so the harness is usable without
a pricing entry.
"""

from decimal import Decimal

_PRICE_PER_MILLION: dict[str, tuple[Decimal, Decimal]] = {
    # (input USD per 1M tokens, output USD per 1M tokens)
    "gpt-4o": (Decimal("2.50"), Decimal("10.00")),
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
    "gpt-4.1": (Decimal("2.00"), Decimal("8.00")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
    "claude-3-5-sonnet-20241022": (Decimal("3.00"), Decimal("15.00")),
    "claude-3-7-sonnet-20250219": (Decimal("3.00"), Decimal("15.00")),
    "claude-sonnet-4-20250514": (Decimal("3.00"), Decimal("15.00")),
    "claude-opus-4-20250514": (Decimal("15.00"), Decimal("75.00")),
}

_DEFAULT_PRICE = (Decimal("0"), Decimal("0"))

_COST_SCALE = Decimal(1_000_000)


def price_for_model(model_id: str) -> tuple[Decimal, Decimal]:
    """Return (input, output) USD per 1M tokens for a model id."""
    return _PRICE_PER_MILLION.get(model_id, _DEFAULT_PRICE)


def compute_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
    """Compute run cost in USD, rounded to 6 decimal places (matches Numeric(12,6))."""
    input_price, output_price = price_for_model(model_id)
    cost = (
        Decimal(prompt_tokens) * input_price + Decimal(completion_tokens) * output_price
    ) / _COST_SCALE
    return cost.quantize(Decimal("0.000001"))
