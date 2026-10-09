"""USD per 1M tokens (input, output). Update as vendors change prices; unknown models fall back."""

from __future__ import annotations

PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (15.0, 75.0),
    "claude-sonnet-5-5": (3.0, 15.0),
    "claude-fable-5-1": (5.0, 25.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4.1-mini": (0.4, 1.6),
    # The offline mock is priced like a small hosted model so dashboards and budgets are demoable.
    "mock-llm": (0.5, 1.5),
}
DEFAULT_PRICE = (3.0, 15.0)


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) used when a provider does not report usage."""
    return max(1, len(text) // 4)


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p_in, p_out = PRICING.get(model, DEFAULT_PRICE)
    return round((input_tokens * p_in + output_tokens * p_out) / 1_000_000, 8)
