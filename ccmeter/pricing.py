"""Pricing tables for Claude models.

Source of truth: Anthropic public pricing pages (cross-referenced April 2026).
- Opus 4.6 / 4.7: $5 / $25 per 1M tokens (input/output)
- Sonnet 4.5 / 4.6: $3 / $15
- Haiku 4.5: $1 / $5

Cache reads are billed at ~10% of standard input rate (90% discount).
Cache writes (cache_creation_input_tokens) are billed at the standard input
rate with a multiplier (1.25x for 5-minute TTL, 2.0x for 1-hour TTL). We
default to 1.25x because that is what Claude Code uses by default.

These prices are CLIENT-SIDE ESTIMATES. Anthropic explicitly notes the
SDK's total_cost_usd is a client-side estimate, not authoritative billing.
We display "estimated" everywhere we use them.

If your model isn't in this table, we fall back to "unknown" and exclude
it from cost (but still count tokens).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModelPrice:
    family: str               # "opus" | "sonnet" | "haiku" | "unknown"
    input_per_mtok: float     # USD per 1M input tokens
    output_per_mtok: float    # USD per 1M output tokens
    cache_read_per_mtok: float
    cache_write_5m_per_mtok: float
    cache_write_1h_per_mtok: float


# Per-million-token prices in USD.
PRICES: dict[str, ModelPrice] = {
    "opus": ModelPrice(
        family="opus",
        input_per_mtok=5.00,
        output_per_mtok=25.00,
        cache_read_per_mtok=0.50,           # 90% off input
        cache_write_5m_per_mtok=6.25,       # 1.25x input
        cache_write_1h_per_mtok=10.00,      # 2.00x input
    ),
    "sonnet": ModelPrice(
        family="sonnet",
        input_per_mtok=3.00,
        output_per_mtok=15.00,
        cache_read_per_mtok=0.30,
        cache_write_5m_per_mtok=3.75,
        cache_write_1h_per_mtok=6.00,
    ),
    "haiku": ModelPrice(
        family="haiku",
        input_per_mtok=1.00,
        output_per_mtok=5.00,
        cache_read_per_mtok=0.10,
        cache_write_5m_per_mtok=1.25,
        cache_write_1h_per_mtok=2.00,
    ),
    "unknown": ModelPrice(
        family="unknown",
        input_per_mtok=0.0,
        output_per_mtok=0.0,
        cache_read_per_mtok=0.0,
        cache_write_5m_per_mtok=0.0,
        cache_write_1h_per_mtok=0.0,
    ),
}


def family_from_model(model: Optional[str]) -> str:
    """Map a model id like 'claude-sonnet-4-5-20250929' to a family key."""
    if not model:
        return "unknown"
    m = model.lower()
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    return "unknown"


def price_for(model: Optional[str]) -> ModelPrice:
    return PRICES[family_from_model(model)]


def estimate_cost_usd(
    model: Optional[str],
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_ttl: str = "5m",
) -> float:
    """Compute estimated USD cost for a single usage record.

    Returns 0.0 for unknown models (we still track tokens elsewhere).
    """
    p = price_for(model)
    write_rate = (
        p.cache_write_1h_per_mtok if cache_ttl == "1h"
        else p.cache_write_5m_per_mtok
    )
    return (
        input_tokens * p.input_per_mtok
        + output_tokens * p.output_per_mtok
        + cache_read_tokens * p.cache_read_per_mtok
        + cache_creation_tokens * write_rate
    ) / 1_000_000.0
