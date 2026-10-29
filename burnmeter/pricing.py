"""Pricing tables for Claude Code + OpenAI Codex models.

Source of truth: official Anthropic + OpenAI pricing pages.
RE-VERIFIED 2026-10-29 against platform.claude.com/docs/en/about-claude/pricing and
developers.openai.com/api/docs/pricing (live fetch). Per 1M tokens
(input / output / cache-read / cache-write-5m / cache-write-1h):
- Fable 5.1 · Mythos 5.1   $10 / $50 / $0.25 / $12.50 / $20    ← cache read 0.025x input
- Fable 5 · Mythos 5       $10 / $50 / $1.00 / $12.50 / $20
- Opus 5.5                 $4  / $20 / $0.20 / $5     / $8     ← cache read 0.05x input
- Opus 5 / 4.5-4.8         $5  / $25 / $0.50 / $6.25  / $10
- Opus 4 / 4.1             $15 / $75 / $1.50 / $18.75 / $30
- Sonnet 5.5               $2  / $10 / $0.10 / $2.50  / $4     ← cache read 0.05x input
- Sonnet 5                 $2  / $10 / $0.20 / $2.50  / $4
- Sonnet 4.x / 3.x         $3  / $15 / $0.30 / $3.75  / $6
- Haiku 5.5, prompt ≤100K  $0.10 / $0.50 / $0.01 / $0.125 / $0.20
- Haiku 5.5, prompt >100K  $0.50 / $2.50 / $0.05 / $0.625 / $1
- Haiku 4.5                $1  / $5  / $0.10 / $1.25  / $2
- Haiku 3.5                $0.80 / $4 / $0.08 / $1    / $1.60
- GPT-5.5 $5/$30 · GPT-5.4 $2.50/$15 · GPT-5.4 mini $0.75/$4.50 · GPT-5 $1.25/$10
  · GPT-5 mini $0.25/$2 · o4-mini $1.10/$4.40 · Codex $1.75/$14
- Fast mode (`usage.speed == "fast"`, Opus 5.5 / 5 / 4.8 only): 2x every rate above.

WHY MODEL IDS ARE PARSED, NOT SUBSTRING-MATCHED (v0.3.5): v0.3.2 made prices
version-aware but still matched versions by substring, so the "opus-5" rule also
matched claude-opus-5-5. Opus 5.5 was billed at Opus 5 rates and marked EXACT:
output +25%, cache reads 2.5x, no warning — on the model a heavy user runs all day.
A Claude id is now parsed into (family, major, minor) and looked up in an exact
table; a version the table doesn't list is priced at the family's newest row and
flagged (see `resolve_price`) so the UI can say "estimated at <family> rates"
instead of quietly inventing a number.

WHY TIERS ARE VERSION-AWARE (v0.3.2): prices used to be looked up by a single
family substring ("sonnet" → one row). When Sonnet 5 shipped at $2/$10 and Fable
5.1 dropped cache reads to $0.25, that generic match silently overstated real
spend by ~24% on a heavy user's month. A price row belongs to a VERSION.

Cache reads are billed well below the input rate (usually 10%, but NOT always —
Fable 5.1 is 2.5%, Opus 5.5 and Sonnet 5.5 are 5%). Cache writes are the input
rate x1.25 (5-minute TTL) or x2.0 (1-hour TTL); the log says which (see parser).

These prices are CLIENT-SIDE ESTIMATES. Anthropic explicitly notes the
SDK's total_cost_usd is a client-side estimate, not authoritative billing.
We display "estimated" everywhere we use them.

If a model isn't in this table at all we fall back to "unknown" and exclude
it from cost (but still count tokens) — and say so in the dashboard.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

# Date the rates below were last checked against the providers' live pricing
# pages. Surfaced in the report so a stale table is visible, not invisible.
PRICES_VERIFIED_AT = "2026-10-29"

# Fast mode (`usage.speed == "fast"`) bills every token type at this multiple of the
# model's standard rates: $8/$40 on Opus 5.5, $10/$50 on Opus 5 and Opus 4.8, with the
# prompt-caching multipliers applied on top. Those three are the only fast models.
FAST_MODE_MULTIPLIER = 2.0


@dataclass(frozen=True)
class ModelPrice:
    family: str               # "opus" | "sonnet" | "haiku" | "unknown"
    input_per_mtok: float     # USD per 1M input tokens
    output_per_mtok: float    # USD per 1M output tokens
    cache_read_per_mtok: float
    cache_write_5m_per_mtok: float
    cache_write_1h_per_mtok: float
    # Prompt-length pricing (Claude Haiku 5.5): a request whose prompt — input + cache
    # reads + cache writes — exceeds `long_prompt_over` tokens pays `long`'s rates on
    # ALL of its tokens, output included. Each request is priced on its own.
    long_prompt_over: int = 0
    long: Optional["ModelPrice"] = None


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
    # Opus 5.5 — cheaper than Opus 5 on every token type, and cache reads are 0.05x
    # input instead of 0.1x. Billing it at the Opus 5 row overstated cache reads 2.5x.
    "opus-5-5": ModelPrice(
        family="opus",
        input_per_mtok=4.00,
        output_per_mtok=20.00,
        cache_read_per_mtok=0.20,           # 95% off input
        cache_write_5m_per_mtok=5.00,
        cache_write_1h_per_mtok=8.00,
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
    # Opus 4 / 4.1 (and Claude 3 Opus) — three times the Opus 4.5+ rates.
    "opus-legacy": ModelPrice(
        family="opus",
        input_per_mtok=15.00,
        output_per_mtok=75.00,
        cache_read_per_mtok=1.50,
        cache_write_5m_per_mtok=18.75,
        cache_write_1h_per_mtok=30.00,
    ),
    # Sonnet 5.5 — Sonnet 5's headline rates, but cache reads at 0.05x input.
    "sonnet-5-5": ModelPrice(
        family="sonnet",
        input_per_mtok=2.00,
        output_per_mtok=10.00,
        cache_read_per_mtok=0.10,
        cache_write_5m_per_mtok=2.50,
        cache_write_1h_per_mtok=4.00,
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
    # Sonnet 4.6 / 4.5 / 4 / 3.x
    "sonnet-legacy": ModelPrice(
        family="sonnet",
        input_per_mtok=3.00,
        output_per_mtok=15.00,
        cache_read_per_mtok=0.30,
        cache_write_5m_per_mtok=3.75,
        cache_write_1h_per_mtok=6.00,
    ),
    # Haiku 5.5 — a tenth of Haiku 4.5 for prompts up to 100K tokens, 5x that above.
    "haiku-5-5": ModelPrice(
        family="haiku",
        input_per_mtok=0.10,
        output_per_mtok=0.50,
        cache_read_per_mtok=0.01,
        cache_write_5m_per_mtok=0.125,
        cache_write_1h_per_mtok=0.20,
        long_prompt_over=100_000,
        long=ModelPrice(
            family="haiku",
            input_per_mtok=0.50,
            output_per_mtok=2.50,
            cache_read_per_mtok=0.05,
            cache_write_5m_per_mtok=0.625,
            cache_write_1h_per_mtok=1.00,
        ),
    ),
    # Haiku 4.5
    "haiku": ModelPrice(
        family="haiku",
        input_per_mtok=1.00,
        output_per_mtok=5.00,
        cache_read_per_mtok=0.10,
        cache_write_5m_per_mtok=1.25,
        cache_write_1h_per_mtok=2.00,
    ),
    "haiku-3-5": ModelPrice(
        family="haiku",
        input_per_mtok=0.80,
        output_per_mtok=4.00,
        cache_read_per_mtok=0.08,
        cache_write_5m_per_mtok=1.00,
        cache_write_1h_per_mtok=1.60,
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
    "gpt-5.4": ModelPrice(
        family="gpt-5.4", input_per_mtok=2.50, output_per_mtok=15.00,
        cache_read_per_mtok=0.25, cache_write_5m_per_mtok=2.50, cache_write_1h_per_mtok=2.50,
    ),
    "gpt-5": ModelPrice(
        family="gpt-5", input_per_mtok=1.25, output_per_mtok=10.00,
        cache_read_per_mtok=0.125, cache_write_5m_per_mtok=1.25, cache_write_1h_per_mtok=1.25,
    ),
    # mini / nano: the VERSION sets the price — gpt-5-mini is a third of gpt-5.4-mini,
    # and o4-mini is dearer than both. One "mini" row billed all three alike.
    "gpt-5.4-mini": ModelPrice(
        family="gpt-5.4-mini", input_per_mtok=0.75, output_per_mtok=4.50,
        cache_read_per_mtok=0.075, cache_write_5m_per_mtok=0.75, cache_write_1h_per_mtok=0.75,
    ),
    "gpt-5.4-nano": ModelPrice(
        family="gpt-5.4-nano", input_per_mtok=0.20, output_per_mtok=1.25,
        cache_read_per_mtok=0.02, cache_write_5m_per_mtok=0.20, cache_write_1h_per_mtok=0.20,
    ),
    "gpt-5-mini": ModelPrice(
        family="gpt-5-mini", input_per_mtok=0.25, output_per_mtok=2.00,
        cache_read_per_mtok=0.025, cache_write_5m_per_mtok=0.25, cache_write_1h_per_mtok=0.25,
    ),
    "gpt-5-nano": ModelPrice(
        family="gpt-5-nano", input_per_mtok=0.05, output_per_mtok=0.40,
        cache_read_per_mtok=0.005, cache_write_5m_per_mtok=0.05, cache_write_1h_per_mtok=0.05,
    ),
    "o4-mini": ModelPrice(
        family="o4-mini", input_per_mtok=1.10, output_per_mtok=4.40,
        cache_read_per_mtok=0.275, cache_write_5m_per_mtok=1.10, cache_write_1h_per_mtok=1.10,
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

# Exact Claude versions: (family, major, minor) → tier. Mythos ids share Fable's rows.
_CLAUDE_VERSIONS: dict[tuple[str, int, int], str] = {
    ("fable", 5, 1): "fable-5-1",
    ("fable", 5, 0): "fable-5",
    ("opus", 5, 5): "opus-5-5",
    ("opus", 5, 0): "opus",
    ("opus", 4, 8): "opus",
    ("opus", 4, 7): "opus",
    ("opus", 4, 6): "opus",
    ("opus", 4, 5): "opus",
    ("opus", 4, 1): "opus-legacy",
    ("opus", 4, 0): "opus-legacy",
    ("opus", 3, 0): "opus-legacy",
    ("sonnet", 5, 5): "sonnet-5-5",
    ("sonnet", 5, 0): "sonnet-5",
    ("sonnet", 4, 6): "sonnet-legacy",
    ("sonnet", 4, 5): "sonnet-legacy",
    ("sonnet", 4, 0): "sonnet-legacy",
    ("sonnet", 3, 7): "sonnet-legacy",
    ("sonnet", 3, 5): "sonnet-legacy",
    ("sonnet", 3, 0): "sonnet-legacy",
    ("haiku", 5, 5): "haiku-5-5",
    ("haiku", 4, 5): "haiku",
    ("haiku", 3, 5): "haiku-3-5",
}
# An unrecognised version of a known family is priced at the family's NEWEST row (a
# new release is far likelier than an ancient one reappearing) and flagged.
_CLAUDE_NEWEST = {"fable": "fable-5-1", "opus": "opus-5-5", "sonnet": "sonnet-5-5", "haiku": "haiku-5-5"}

# claude-opus-4-5-20251101 · claude-opus-5-5 · us.anthropic.claude-opus-4-1-20250805-v1:0
# · claude-opus-4-1@20250805. The (?!\d) guard keeps an 8-digit snapshot date from
# being read as a minor version: claude-opus-4-20250514 is Opus 4, not Opus 4.20.
_CLAUDE_ID = re.compile(r"(fable|mythos|opus|sonnet|haiku)-?(\d)(?:[-.]?(\d{1,2}))?(?!\d)")
# The Claude 3.x scheme puts the version first: claude-3-5-sonnet-20241022, claude-3-opus.
_CLAUDE_ID_V3 = re.compile(r"(?<!\d)(\d)(?:[-.](\d))?-(opus|sonnet|haiku)")
_CLAUDE_FAMILIES = ("fable", "mythos", "opus", "sonnet", "haiku")


def _parse_claude_id(m: str) -> Optional[tuple[str, Optional[tuple[int, int]]]]:
    """'claude-opus-5-5' → ('opus', (5, 5)); a bare family word → (family, None);
    no Claude family at all → None."""
    hit = _CLAUDE_ID.search(m)
    if hit:
        fam = "fable" if hit.group(1) == "mythos" else hit.group(1)
        return fam, (int(hit.group(2)), int(hit.group(3) or 0))
    hit = _CLAUDE_ID_V3.search(m)
    if hit:
        return hit.group(3), (int(hit.group(1)), int(hit.group(2) or 0))
    for fam in _CLAUDE_FAMILIES:
        if fam in m:
            return ("fable" if fam == "mythos" else fam), None
    return None


@lru_cache(maxsize=512)
def display_name(model: Optional[str]) -> str:
    """'claude-opus-5-5' → 'Opus 5.5', 'claude-opus-4-20250514' → 'Opus 4',
    'claude-3-5-sonnet-20241022' → 'Sonnet 3.5'. Ids outside the Claude families
    come back unchanged ('gpt-5.5-codex'); a missing id becomes '—'."""
    if not model:
        return "—"
    m = model.lower()
    claude = _parse_claude_id(m)
    if claude is None:
        return model
    fam, version = claude
    name = "Mythos" if "mythos" in m else fam.capitalize()
    if version is None:
        return name
    major, minor = version
    return f"{name} {major}.{minor}" if minor else f"{name} {major}"

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
    claude = _parse_claude_id(m)
    if claude is not None:
        fam, version = claude
        tier = _CLAUDE_VERSIONS.get((fam, *version)) if version else None
        return (tier, True) if tier else (_CLAUDE_NEWEST[fam], False)
    # --- OpenAI / Codex. Variant beats version: gpt-5.3-codex is a Codex model
    # and gpt-5.4-mini is a mini model — checking the version number first would
    # bill both at the flagship tier. ('mini' only counts on OpenAI ids.)
    if "codex" in m:
        return "gpt-5-codex", True
    if ("mini" in m or "nano" in m) and ("gpt" in m or m.startswith("o")):
        size = "nano" if "nano" in m else "mini"
        if m.startswith("o4-mini"):
            return "o4-mini", True
        if "5.4" in m or "5-4" in m:
            return f"gpt-5.4-{size}", True
        if re.match(r"gpt-?5-(mini|nano)", m):
            return f"gpt-5-{size}", True
        return f"gpt-5.4-{size}", False   # unlisted mini/nano version → newest known, flagged
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
    if re.fullmatch(r"gpt-?5(-\d{4}-\d{2}-\d{2})?", m):   # plain gpt-5, optionally dated
        return "gpt-5", True
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


def _tier(p: ModelPrice, prompt_tokens: int) -> ModelPrice:
    """The rates a request of this prompt length pays (Haiku 5.5 doubles past 100K)."""
    return p.long if p.long is not None and prompt_tokens > p.long_prompt_over else p


def record_price(rec) -> tuple[ModelPrice, float]:
    """The rates one record was billed at, and its fast-mode factor."""
    p = _tier(price_for(rec.model),
              rec.input_tokens + rec.cache_read_tokens + rec.cache_creation_tokens)
    return p, (FAST_MODE_MULTIPLIER if getattr(rec, "speed", "") == "fast" else 1.0)


def estimate_cost_usd(
    model: Optional[str],
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_ttl: str = "5m",
    cache_creation_1h_tokens: int = 0,
    speed: str = "",
) -> float:
    """Compute estimated USD cost for a single usage record.

    `cache_creation_tokens` is the TOTAL written to cache; `cache_creation_1h_tokens`
    is how much of that used the 1-hour TTL, which bills at 2.0x input instead of
    1.25x. Claude Code writes most of its cache at 1h in agentic sessions, so
    ignoring the split under-reports real spend by a wide margin. Records from
    before the log format carried the split pass 0 and bill at 5m, as they did.

    `speed="fast"` (fast mode) doubles every rate. Prompt-length tiers (Haiku 5.5)
    are decided per call, so pass ONE request's tokens here, never a sum.

    Returns 0.0 for unknown models (we still track tokens elsewhere).
    """
    p = _tier(price_for(model), input_tokens + cache_read_tokens + cache_creation_tokens)
    write_1h = min(max(int(cache_creation_1h_tokens), 0), cache_creation_tokens)
    if not write_1h and cache_ttl == "1h":
        write_1h = cache_creation_tokens        # caller declared the whole write as 1h
    write_5m = cache_creation_tokens - write_1h
    cost = (
        input_tokens * p.input_per_mtok
        + output_tokens * p.output_per_mtok
        + cache_read_tokens * p.cache_read_per_mtok
        + write_5m * p.cache_write_5m_per_mtok
        + write_1h * p.cache_write_1h_per_mtok
    ) / 1_000_000.0
    return cost * FAST_MODE_MULTIPLIER if speed == "fast" else cost


def record_cost_usd(rec) -> float:
    """Cost of one parsed usage record (= one API response).

    Use this rather than spelling out estimate_cost_usd's fields at each call site:
    every billing input (1h writes, fast mode, prompt-length tiers) is per request,
    and a call site that forgets one misprices silently.
    """
    return estimate_cost_usd(
        rec.model,
        input_tokens=rec.input_tokens,
        output_tokens=rec.output_tokens,
        cache_read_tokens=rec.cache_read_tokens,
        cache_creation_tokens=rec.cache_creation_tokens,
        cache_creation_1h_tokens=getattr(rec, "cache_creation_1h_tokens", 0),
        speed=getattr(rec, "speed", ""),
    )


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
