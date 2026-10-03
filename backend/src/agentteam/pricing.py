"""Model pricing in USD per million tokens: (input, output).

VERIFY these against Anthropic's pricing page and add the models you use. Unknown
models fall back to a deliberately high price so the spend cap trips early rather
than late.
"""

PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "fake": (0.0, 0.0),  # FakeLLM must never count against the spend cap
}

FALLBACK_USD_PER_MTOK: tuple[float, float] = (15.00, 75.00)


def compute_cost(model: str | None, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICING_USD_PER_MTOK.get(model or "", FALLBACK_USD_PER_MTOK)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000
