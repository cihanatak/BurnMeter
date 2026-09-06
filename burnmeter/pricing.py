"""Pricing tables for Claude Code + OpenAI Codex models.

Source of truth: official Anthropic + OpenAI pricing pages.
RE-VERIFIED 2026-09-06 against claude.com/pricing (live fetch) and
openai.com/api/pricing. Per 1M tokens (input / output / cache-read / cache-write-5m):
- Fable 5.1 · Mythos 5.1  $10 / $50 / $0.25 / $12.50   ← cache read is 4x cheaper
- Fable 5 · Mythos 5      $10 / $50 / $1.00 / $12.50     than Fable 5's (legacy row)
- Opus 5 / 4.5-4.8        $5  / $25 / $0.50 / $6.25
- Sonnet 5                $2  / $10 / $0.20 / $2.50     ← cheaper than Sonnet 4.x
- Sonnet 4.x / 3.x        $3  / $15 / $0.30 / $3.75
- Haiku 4.5               $1  / $5  / $0.10 / $1.25
- GPT-5.5 $5/$30 · GPT-5.4 $2.50/$15 · GPT-5-mini $0.75/$4.50 · Codex $1.75/$14

WHY TIERS ARE VERSION-AWARE (v0.3.2): prices used to be looked up by a single
family substring ("sonnet" → one row). When Sonnet 5 shipped at $2/$10 and Fable
5.1 dropped cache reads to $0.25, that generic match silently overstated real
spend by ~24% on a heavy user's month. A price row now belongs to a VERSION, and
anything we can't match exactly is flagged (see `resolve_price`) so the UI can say
"estimated at <family> rates" instead of quietly inventing a number.

Cache reads are billed well below the input rate (usually 10%, but NOT always —
Fable 5.1 is 2.5%). Cache writes are the input rate x1.25 (5-minute TTL) or x2.0
(1-hour TTL); Claude Code defaults to 5-minute.

These prices are CLIENT-SIDE ESTIMATES. Anthropic explicitly notes the
SDK's total_cost_usd is a client-side estimate, not authoritative billing.
We display "estimated" everywhere we use them.

If a model isn't in this table at all we fall back to "unknown" and exclude
it from cost (but still count tokens) — and say so in the dashboard.
"""
from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

# Date the rates below were last checked against the providers' live pricing
# pages. Surfaced in the report so a stale table is visible, not invisible.
PRICES_VERIFIED_AT = "2026-09-06"


@dataclass(frozen=True)
class ModelPrice:
    family: str               # "opus" | "sonnet" | "haiku" | "unknown"
    input_per_mtok: float     # USD per 1M input tokens
    output_per_mtok: float    # USD per 1M output tokens
    cache_read_per_mtok: float
    cache_write_5m_per_mtok: float
    cache_write_1h_per_mtok: float


# Per-million-token prices in USD, keyed by PRICE TIER (a version, not a family:
# two versions of one family can bill differently — see the module docstring).
PRICES: dict[str, ModelPrice] = {
    # Fable 5.1 / Mythos 5.1 — same headline rates as Fable 5 but cache reads are
    # $0.25/MTok instead of $1.00. Cache reads dominate agentic sessions, so this
    # single number moves a heavy user's monthly total by hundreds of dollars.
    "fable-5-1": ModelPrice(
        family="fable-5",
        input_per_mtok=10.00,
        output_per_mtok=50.00,
        cache_read_per_mtok=0.25,
        cache_write_5m_per_mtok=12.50,      # 1.25x input
        cache_write_1h_per_mtok=20.00,      # 2.00x input
    ),
    # Fable 5 / Mythos 5 (legacy row — kept because it is still served)
    "fable-5": ModelPrice(
        family="fable-5",
        input_per_mtok=10.00,
        output_per_mtok=50.00,
        cache_read_per_mtok=1.00,           # 90% off input
        cache_write_5m_per_mtok=12.50,
        cache_write_1h_per_mtok=20.00,
    ),
    # Opus 5 and Opus 4.5-4.8 share one price point.
    "opus": ModelPrice(
        family="opus",
        input_per_mtok=5.00,
        output_per_mtok=25.00,
        cache_read_per_mtok=0.50,           # 90% off input
        cache_write_5m_per_mtok=6.25,       # 1.25x input
        cache_write_1h_per_mtok=10.00,      # 2.00x input
    ),
    # Sonnet 5 — cheaper than the Sonnet 4.x line it replaced.
    "sonnet-5": ModelPrice(
        family="sonnet",
        input_per_mtok=2.00,
        output_per_mtok=10.00,
        cache_read_per_mtok=0.20,
        cache_write_5m_per_mtok=2.50,
        cache_write_1h_per_mtok=4.00,
    ),
    # Sonnet 4.6 / 4.5 / 3.x
    "sonnet-legacy": ModelPrice(
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
    # --- OpenAI / Codex --------------------------------------------------------
    # Real OpenAI public API pricing. Cached input = 10% of input (90% off), like
    # Claude. OpenAI has no explicit cache WRITE charge (automatic prompt cache,
    # only reads are discounted) → cache_write_* = input rate (a no-op anyway
    # because Codex records carry cache_creation = 0).
    # GPT-6 Astra — the flagship above GPT-5.5. Twice 5.5's rate on both sides, so
    # falling back to the 5.5 row halved a Codex user's reported spend.
    "gpt-6-astra": ModelPrice(
        family="gpt-6-astra", input_per_mtok=10.00, output_per_mtok=50.00,
        cache_read_per_mtok=1.00, cache_write_5m_per_mtok=10.00, cache_write_1h_per_mtok=10.00,
    ),
    # GPT-5.6 ships as three tiers that differ by 20x. Luna in particular was being
    # reported at 25x its real cost — a user switching to it to save money would
    # have watched Burnmeter claim their bill exploded.
    "gpt-5.6-sol": ModelPrice(
        family="gpt-5.6-sol", input_per_mtok=4.00, output_per_mtok=20.00,
        cache_read_per_mtok=0.40, cache_write_5m_per_mtok=4.00, cache_write_1h_per_mtok=4.00,
    ),
    "gpt-5.6-terra": ModelPrice(
        family="gpt-5.6-terra", input_per_mtok=2.00, output_per_mtok=12.00,
        cache_read_per_mtok=0.20, cache_write_5m_per_mtok=2.00, cache_write_1h_per_mtok=2.00,
    ),
    "gpt-5.6-luna": ModelPrice(
        family="gpt-5.6-luna", input_per_mtok=0.20, output_per_mtok=1.20,
        cache_read_per_mtok=0.02, cache_write_5m_per_mtok=0.20, cache_write_1h_per_mtok=0.20,
    ),
    "gpt-5.5-pro": ModelPrice(
        family="gpt-5.5-pro", input_per_mtok=30.00, output_per_mtok=180.00,
        cache_read_per_mtok=3.00, cache_write_5m_per_mtok=30.00, cache_write_1h_per_mtok=30.00,
    ),
    "gpt-5.5": ModelPrice(
        family="gpt-5.5", input_per_mtok=5.00, output_per_mtok=30.00,
        cache_read_per_mtok=0.50, cache_write_5m_per_mtok=5.00, cache_write_1h_per_mtok=5.00,
    ),
    "gpt-5-nano": ModelPrice(
        family="gpt-5-nano", input_per_mtok=0.20, output_per_mtok=1.25,
        cache_read_per_mtok=0.02, cache_write_5m_per_mtok=0.20, cache_write_1h_per_mtok=0.20,
    ),
    "gpt-5.4": ModelPrice(
        family="gpt-5.4", input_per_mtok=2.50, output_per_mtok=15.00,
        cache_read_per_mtok=0.25, cache_write_5m_per_mtok=2.50, cache_write_1h_per_mtok=2.50,
    ),
    "gpt-5-mini": ModelPrice(
        family="gpt-5-mini", input_per_mtok=0.75, output_per_mtok=4.50,
        cache_read_per_mtok=0.075, cache_write_5m_per_mtok=0.75, cache_write_1h_per_mtok=0.75,
    ),
    "gpt-5-codex": ModelPrice(
        family="gpt-5-codex", input_per_mtok=1.75, output_per_mtok=14.00,
        cache_read_per_mtok=0.175, cache_write_5m_per_mtok=1.75, cache_write_1h_per_mtok=1.75,
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

# Ordered match rules: (substrings, tier, exact). FIRST match wins, so every
# version-specific rule must come BEFORE the generic family rule it shadows.
# exact=True  → we recognise this exact model generation; the price is trusted.
# exact=False → a model of a known family whose VERSION we don't know (e.g. a
#               brand-new "claude-opus-6"). We price it at the family's current
#               generation and FLAG it, so the dashboard can say the number is a
#               best guess rather than pretending it is authoritative.
_TIER_RULES: tuple[tuple[tuple[str, ...], str, bool], ...] = (
    # --- Anthropic, version-specific ---
    (("fable-5-1", "fable-5.1", "fable51", "mythos-5-1", "mythos-5.1"), "fable-5-1", True),
    (("fable-5", "fable5", "mythos-5", "mythos5"), "fable-5", True),
    (("opus-5", "opus5", "opus-4", "opus4"), "opus", True),
    (("sonnet-5", "sonnet5"), "sonnet-5", True),
    # Sonnet 4.x and the old "claude-3-5-sonnet" naming scheme share the $3/$15 band.
    (("sonnet-4", "sonnet4", "3-5-sonnet", "3.5-sonnet", "3-7-sonnet", "3.7-sonnet"),
     "sonnet-legacy", True),
    (("haiku-4", "haiku4"), "haiku", True),
    # --- Anthropic, family fallbacks: an unrecognised version of a known family.
    # Priced at the family's CURRENT generation (a new release is far likelier
    # than an ancient one reappearing) and flagged as estimated.
    (("fable", "mythos"), "fable-5-1", False),
    (("opus",), "opus", False),
    (("sonnet",), "sonnet-5", False),
    (("haiku",), "haiku", False),
)


@lru_cache(maxsize=512)
def resolve_price(model: Optional[str]) -> tuple[str, bool]:
    """Map a model id to (price tier, is_exact).

    is_exact=False means "we priced this by family, not by version" — the caller
    should tell the user the figure is an estimate. Internal Claude Code markers
    like '<synthetic>' cost nothing and are reported as exact so they never raise
    a false alarm.
    """
    if not model:
        return "unknown", True
    m = model.lower()
    if m.startswith("<"):            # <synthetic> et al: internal, genuinely $0
        return "unknown", True
    for needles, tier, exact in _TIER_RULES:
        if any(n in m for n in needles):
            return tier, exact
    # --- OpenAI / Codex. Variant beats version: gpt-5.3-codex is a Codex model
    # and gpt-5.4-mini is a mini model — checking the version number first would
    # bill both at the flagship tier. ('mini' only counts on OpenAI ids.)
    if "codex" in m:
        return "gpt-5-codex", True
    if "mini" in m and ("gpt" in m or m.startswith("o")):
        return "gpt-5-mini", True
    if "nano" in m and ("gpt" in m or m.startswith("o")):
        return "gpt-5-nano", True
    if "astra" in m or "gpt-6" in m or "gpt6" in m:
        return "gpt-6-astra", True
    # GPT-5.6's three tiers differ by 20x, so the tier name decides the price.
    if "luna" in m:
        return "gpt-5.6-luna", True
    if "terra" in m:
        return "gpt-5.6-terra", True
    if "sol" in m:
        return "gpt-5.6-sol", True
    if "gpt-5.6" in m or "gpt5.6" in m:
        return "gpt-5.6-sol", False  # unnamed 5.6 tier → priciest of the three, flagged
    if "gpt-5.5-pro" in m or "gpt5.5-pro" in m:
        return "gpt-5.5-pro", True
    if "gpt-5.5" in m or "gpt5.5" in m:
        return "gpt-5.5", True
    if "gpt-5.4" in m or "gpt5.4" in m:
        return "gpt-5.4", True
    if "gpt" in m or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return "gpt-5.5", False      # unknown GPT version → flagship tier, flagged
    return "unknown", False


@lru_cache(maxsize=512)
def family_from_model(model: Optional[str]) -> str:
    """Map a model id to its DISPLAY family ('opus', 'sonnet', 'fable-5', ...).

    This is the grouping key used across analytics and the dashboard, so it stays
    family-level on purpose: Sonnet 5 and Sonnet 4.6 bill differently but belong
    in one "Sonnet" row for the user. Cost lookups go through resolve_price().

    @lru_cache: pure function of the model string. It is called ~10M times per
    build (every aggregation x every record) over ~10 distinct models, so the
    string matching happens once and the rest are O(1) hits (codex build 11s→7s).
    """
    tier, _ = resolve_price(model)
    return PRICES[tier].family


@lru_cache(maxsize=512)
def price_for(model: Optional[str]) -> ModelPrice:
    return PRICES[resolve_price(model)[0]]


def is_price_estimated(model: Optional[str]) -> bool:
    """True when this model's rate came from a family fallback, not a known version."""
    return not resolve_price(model)[1]


def estimate_cost_usd(
    model: Optional[str],
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_ttl: str = "5m",
    cache_creation_1h_tokens: int = 0,
) -> float:
    """Compute estimated USD cost for a single usage record.

    `cache_creation_tokens` is the TOTAL written to cache; `cache_creation_1h_tokens`
    is how much of that used the 1-hour TTL, which bills at 2.0x input instead of
    1.25x. Claude Code writes most of its cache at 1h in agentic sessions, so
    ignoring the split under-reports real spend by a wide margin. Records from
    before the log format carried the split pass 0 and bill at 5m, as they did.

    Returns 0.0 for unknown models (we still track tokens elsewhere).
    """
    p = price_for(model)
    write_1h = min(max(int(cache_creation_1h_tokens), 0), cache_creation_tokens)
    if not write_1h and cache_ttl == "1h":
        write_1h = cache_creation_tokens        # caller declared the whole write as 1h
    write_5m = cache_creation_tokens - write_1h
    return (
        input_tokens * p.input_per_mtok
        + output_tokens * p.output_per_mtok
        + cache_read_tokens * p.cache_read_per_mtok
        + write_5m * p.cache_write_5m_per_mtok
        + write_1h * p.cache_write_1h_per_mtok
    ) / 1_000_000.0


def effective_tokens(
    model: Optional[str],
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_creation_1h_tokens: int = 0,
) -> int:
    """Cost-weighted input-equivalent token count.

    A raw token total misleads: cache reads are ~10x cheaper than fresh input and
    output is several times more expensive. Each token type is therefore scaled to
    its OWN model's input price (multiplier = price_per_tok / input_price), giving
    one input-equivalent number that is comparable across models.

    - Claude Opus: output 5x, cache_creation 1.25x, cache_read 0.10x
    - OpenAI gpt5: output 8x, cache_creation —, cache_read 0.10x
    Derived from the pricing table, so it stays model-agnostic.
    """
    p = price_for(model)
    base = p.input_per_mtok or 1.0   # division guard; unknown → zero cost anyway
    if p.input_per_mtok <= 0:
        # unknown model: raw fresh tokens (in+out), unweighted
        return int(input_tokens + output_tokens)
    out_mult = p.output_per_mtok / base
    cr_mult = p.cache_read_per_mtok / base
    cc_mult = p.cache_write_5m_per_mtok / base
    cc1_mult = p.cache_write_1h_per_mtok / base
    write_1h = min(max(int(cache_creation_1h_tokens), 0), cache_creation_tokens)
    write_5m = cache_creation_tokens - write_1h
    return int(
        input_tokens
        + output_tokens * out_mult
        + cache_read_tokens * cr_mult
        + write_5m * cc_mult
        + write_1h * cc1_mult
    )
