"""Model pricing in USD per million tokens, including prompt-caching prices.

Source: Anthropic's prompt-caching pricing table (checked 2026-10-03). Re-verify when you add a
model or when prices change. Unknown models fall back to a deliberately high price so the spend
cap trips early rather than late.

Cache pricing multipliers (5-minute cache, the default we use):
- cache WRITE tokens cost 1.25x the base input price
- cache READ tokens cost `read_mult` x the base input price: 0.1x for most models, 0.05x for
  Opus 5.5 and 0.025x for Fable 5.1 / Mythos 5.1
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float  # USD per million input tokens
    output: float  # USD per million output tokens
    read_mult: float = 0.1  # cache-read price as a multiple of `input`


CACHE_WRITE_MULT = 1.25

PRICING: dict[str, Price] = {
    "claude-haiku-4-5-20251001": Price(1.00, 5.00),
    "claude-sonnet-5-5": Price(2.00, 10.00),
    "claude-opus-5-5": Price(4.00, 20.00, read_mult=0.05),
    "claude-fable-5-1": Price(10.00, 50.00, read_mult=0.025),
    "fake": Price(0.0, 0.0),  # FakeLLM must never count against the spend cap
}

FALLBACK = Price(15.00, 75.00)


def compute_cost(
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """`input_tokens` is the UNCACHED input only (what the API reports as input_tokens); cache
    writes and reads are billed separately, at their own prices."""
    p = PRICING.get(model or "", FALLBACK)
    return (
        input_tokens * p.input
        + output_tokens * p.output
        + cache_write_tokens * p.input * CACHE_WRITE_MULT
        + cache_read_tokens * p.input * p.read_mult
    ) / 1_000_000
