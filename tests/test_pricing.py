"""Pricing correctness — the numbers this product exists to get right.

Why this file exists: v0.3.1 shipped with Sonnet 5 billed at Sonnet 4.x rates
(+50%) and Fable 5.1 cache reads at 4x the real rate, overstating a heavy user's
month by ~24%. Both slipped through 74 passing tests because NOTHING asserted
that a model maps to the right price — only that the arithmetic was consistent.

Rates below were verified against claude.com/pricing on 2026-09-06. When a
provider changes a price or ships a model, update PRICES *and* this file
together; a failure here means the table drifted from reality.
"""
from __future__ import annotations

import json

import pytest

from burnmeter import parser
from burnmeter.analytics import pricing_coverage
from burnmeter.parser import parse_line
from burnmeter.pricing import (
    PRICES,
    PRICES_VERIFIED_AT,
    effective_tokens,
    estimate_cost_usd,
    family_from_model,
    is_price_estimated,
    price_for,
    resolve_price,
)

# model id -> (input, output, cache_read, cache_write_5m) per 1M tokens
LIVE_RATES = {
    "claude-opus-5":              (5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-8":            (5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-5-20251101":   (5.00, 25.00, 0.50, 6.25),
    "claude-fable-5-1":           (10.00, 50.00, 0.25, 12.50),
    "claude-mythos-5-1":          (10.00, 50.00, 0.25, 12.50),
    "claude-fable-5":             (10.00, 50.00, 1.00, 12.50),
    "claude-sonnet-5":            (2.00, 10.00, 0.20, 2.50),
    "claude-sonnet-4-6":          (3.00, 15.00, 0.30, 3.75),
    "claude-sonnet-4-5-20250929": (3.00, 15.00, 0.30, 3.75),
    "claude-3-5-sonnet-20241022": (3.00, 15.00, 0.30, 3.75),
    "claude-haiku-4-5-20251001":  (1.00, 5.00, 0.10, 1.25),
    "gpt-6-astra":                (10.00, 50.00, 1.00, 10.00),
    "gpt-5.6-sol":                (4.00, 20.00, 0.40, 4.00),
    "gpt-5.6-terra":              (2.00, 12.00, 0.20, 2.00),
    "gpt-5.6-luna":               (0.20, 1.20, 0.02, 0.20),
    "gpt-5.5-pro":                (30.00, 180.00, 3.00, 30.00),
    "gpt-5.5":                    (5.00, 30.00, 0.50, 5.00),
    "gpt-5.4":                    (2.50, 15.00, 0.25, 2.50),
    "gpt-5.4-mini":               (0.75, 4.50, 0.075, 0.75),
    "gpt-5.4-nano":               (0.20, 1.25, 0.02, 0.20),
    "gpt-5.3-codex":              (1.75, 14.00, 0.175, 1.75),
    "o4-mini":                    (0.75, 4.50, 0.075, 0.75),
}


@pytest.mark.parametrize("model,rates", sorted(LIVE_RATES.items()))
def test_rates_match_the_live_price_pages(model, rates):
    p = price_for(model)
    assert (p.input_per_mtok, p.output_per_mtok,
            p.cache_read_per_mtok, p.cache_write_5m_per_mtok) == rates, model


@pytest.mark.parametrize("model", sorted(LIVE_RATES))
def test_every_shipping_model_is_recognised_exactly(model):
    """A model we ship support for must never be priced by family guesswork."""
    assert not is_price_estimated(model), f"{model} fell back to a family rate"


def test_sonnet_5_is_cheaper_than_sonnet_4():
    """The v0.3.1 bug: one 'sonnet' row billed Sonnet 5 at the 4.x rate (+50%)."""
    assert price_for("claude-sonnet-5").input_per_mtok < price_for("claude-sonnet-4-6").input_per_mtok
    assert estimate_cost_usd("claude-sonnet-5", input_tokens=1_000_000) == pytest.approx(2.00)
    assert estimate_cost_usd("claude-sonnet-4-6", input_tokens=1_000_000) == pytest.approx(3.00)


def test_fable_51_cache_reads_are_a_quarter_of_fable_5():
    """The other v0.3.1 bug. Cache reads dominate agentic usage, so a 4x error
    here moved a real month by thousands of dollars."""
    assert estimate_cost_usd("claude-fable-5-1", cache_read_tokens=1_000_000) == pytest.approx(0.25)
    assert estimate_cost_usd("claude-fable-5", cache_read_tokens=1_000_000) == pytest.approx(1.00)


def test_variant_beats_version_for_openai_ids():
    """gpt-5.4-mini is a mini model, not the 5.4 flagship; same for codex/nano."""
    assert resolve_price("gpt-5.4-mini")[0] == "gpt-5-mini"
    assert resolve_price("gpt-5.4-nano")[0] == "gpt-5-nano"
    assert resolve_price("gpt-5.3-codex")[0] == "gpt-5-codex"
    assert resolve_price("gpt-5.5-pro")[0] == "gpt-5.5-pro"   # must beat plain gpt-5.5


def test_gpt56_tiers_are_not_collapsed_into_one_price():
    """Sol/Terra/Luna differ by 20x. Pricing Luna as the flagship reported a
    cost-cutting user's spend at ~25x reality — the tool would look broken."""
    luna = estimate_cost_usd("gpt-5.6-luna", input_tokens=1_000_000)
    terra = estimate_cost_usd("gpt-5.6-terra", input_tokens=1_000_000)
    sol = estimate_cost_usd("gpt-5.6-sol", input_tokens=1_000_000)
    assert (luna, terra, sol) == (pytest.approx(0.20), pytest.approx(2.00), pytest.approx(4.00))
    assert luna < terra < sol < estimate_cost_usd("gpt-6-astra", input_tokens=1_000_000)


def test_gpt6_astra_is_not_priced_as_gpt55():
    """Astra bills at twice GPT-5.5 on both sides; the generic 'gpt' fallback halved it."""
    assert estimate_cost_usd("gpt-6-astra", input_tokens=1_000_000, output_tokens=1_000_000) == \
        pytest.approx(60.00)
    assert resolve_price("gpt-6-astra")[0] == "gpt-6-astra"


def test_codex_turns_with_no_model_are_unknown_not_a_fabricated_flagship():
    """codex_parser used to stamp unresolved turns 'gpt-5', pricing them at a
    flagship rate the user may never have touched. Empty → unknown → surfaced."""
    assert resolve_price("") == ("unknown", True)
    assert estimate_cost_usd("", input_tokens=1_000_000) == 0.0


def test_specific_version_beats_generic_family():
    """fable-5-1 must win over the fable-5 substring it contains."""
    assert resolve_price("claude-fable-5-1")[0] == "fable-5-1"
    assert resolve_price("claude-opus-5")[0] == "opus"


def test_unknown_future_model_is_priced_by_family_and_flagged():
    tier, exact = resolve_price("claude-opus-9-turbo")
    assert tier == "opus" and exact is False        # costed, but not silently
    assert is_price_estimated("claude-opus-9-turbo")


def test_completely_unknown_model_costs_nothing_and_is_flagged():
    tier, exact = resolve_price("some-other-vendor-model")
    assert tier == "unknown" and exact is False
    assert estimate_cost_usd("some-other-vendor-model", input_tokens=1_000_000) == 0.0


def test_internal_markers_are_free_and_never_warn():
    """'<synthetic>' is a Claude Code internal marker with no API cost — warning
    about it would cry wolf on every single report."""
    assert resolve_price("<synthetic>") == ("unknown", True)
    assert not is_price_estimated("<synthetic>")


def test_display_families_are_unchanged_by_the_tier_split():
    """Tiers are per-version; the grouping key the UI uses stays per-family."""
    assert family_from_model("claude-sonnet-5") == "sonnet"
    assert family_from_model("claude-sonnet-4-6") == "sonnet"
    assert family_from_model("claude-fable-5-1") == "fable-5"
    assert family_from_model("claude-opus-5") == "opus"
    assert family_from_model(None) == "unknown"


def test_every_price_tier_is_reachable():
    """A tier no rule can select is dead weight that will silently rot."""
    reachable = {resolve_price(m)[0] for m in LIVE_RATES}
    reachable |= {"unknown"}
    assert set(PRICES) - reachable == set(), "unreachable price tiers"


def test_pricing_coverage_reports_estimates_and_unpriced():
    bmf = [
        {"model_id": "claude-opus-5", "cost_usd": 10.0, "total_tokens": 100},
        {"model_id": "claude-opus-9-turbo", "cost_usd": 4.0, "total_tokens": 40},
        {"model_id": "mystery-model", "cost_usd": 0.0, "total_tokens": 7},
        {"model_id": "<synthetic>", "cost_usd": 0.0, "total_tokens": 3},
    ]
    cov = pricing_coverage(bmf)
    assert [r["model_id"] for r in cov["estimated"]] == ["claude-opus-9-turbo"]
    assert [r["model_id"] for r in cov["unpriced"]] == ["mystery-model"]
    assert cov["estimated_cost_usd"] == pytest.approx(4.0)
    assert cov["unpriced_tokens"] == 7
    assert cov["verified_at"] == PRICES_VERIFIED_AT


def test_clean_report_raises_no_warning():
    cov = pricing_coverage([{"model_id": "claude-opus-5", "cost_usd": 1.0, "total_tokens": 10}])
    assert cov["estimated"] == [] and cov["unpriced"] == []


# --- 1-hour cache-write TTL -------------------------------------------------
# Claude Code writes most of its cache with the 1h TTL, which bills at 2.0x input
# instead of 1.25x. v0.3.2 and earlier billed every write at 5m, under-reporting
# real spend (on the maker's own month, ~80% of writes were 1h).

def test_one_hour_cache_writes_cost_more_than_five_minute():
    five = estimate_cost_usd("claude-opus-5", cache_creation_tokens=1_000_000)
    one_h = estimate_cost_usd("claude-opus-5", cache_creation_tokens=1_000_000,
                              cache_creation_1h_tokens=1_000_000)
    assert five == pytest.approx(6.25)      # 1.25x $5 input
    assert one_h == pytest.approx(10.00)    # 2.00x $5 input
    assert one_h > five


def test_mixed_ttl_writes_are_split_not_rounded():
    cost = estimate_cost_usd("claude-opus-5", cache_creation_tokens=1_000_000,
                             cache_creation_1h_tokens=400_000)
    assert cost == pytest.approx(0.6 * 6.25 + 0.4 * 10.00)


def test_records_without_the_split_still_bill_at_five_minutes():
    """Logs written before the field existed must price exactly as they used to."""
    assert estimate_cost_usd("claude-opus-5", cache_creation_tokens=1_000_000,
                             cache_creation_1h_tokens=0) == pytest.approx(6.25)


def test_one_hour_split_cannot_exceed_the_total_written():
    """A malformed record must never bill more cache than it wrote."""
    assert estimate_cost_usd("claude-opus-5", cache_creation_tokens=1_000,
                             cache_creation_1h_tokens=999_999) == pytest.approx(
        estimate_cost_usd("claude-opus-5", cache_creation_tokens=1_000,
                          cache_creation_1h_tokens=1_000))
    assert estimate_cost_usd("claude-opus-5", cache_creation_tokens=1_000,
                             cache_creation_1h_tokens=-5) == pytest.approx(0.00625)


def test_effective_tokens_weights_one_hour_writes_higher():
    """The cost-weighted token count must follow the same split, or the 'effective
    tokens' figure silently disagrees with the dollar figure beside it."""
    five = effective_tokens("claude-opus-5", cache_creation_tokens=1_000_000)
    one_h = effective_tokens("claude-opus-5", cache_creation_tokens=1_000_000,
                             cache_creation_1h_tokens=1_000_000)
    assert one_h > five
    assert one_h == pytest.approx(2_000_000, rel=1e-6)   # 2.00x input-equivalent


def test_parser_reads_the_ttl_split_from_a_real_log_shape():
    """Guards the exact JSON shape Claude Code writes today."""
    line = json.dumps({
        "type": "assistant",
        "timestamp": "2026-09-06T10:00:00.000Z",
        "sessionId": "s1",
        "uuid": "u1",
        "cwd": "/tmp/proj",
        "message": {
            "id": "m1",
            "role": "assistant",
            "model": "claude-opus-5",
            "usage": {
                "input_tokens": 2,
                "output_tokens": 1,
                "cache_read_input_tokens": 37012,
                "cache_creation_input_tokens": 20985,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 5985,
                    "ephemeral_1h_input_tokens": 15000,
                },
            },
        },
    })
    rec = parse_line(line)
    assert rec is not None
    assert rec.cache_creation_tokens == 20985
    assert rec.cache_creation_1h_tokens == 15000


def test_parser_tolerates_logs_without_the_split():
    line = json.dumps({
        "type": "assistant",
        "timestamp": "2026-09-06T10:00:00.000Z",
        "sessionId": "s1",
        "uuid": "u1",
        "cwd": "/tmp/proj",
        "message": {"id": "m2", "role": "assistant", "model": "claude-opus-5",
                    "usage": {"input_tokens": 1, "output_tokens": 1,
                              "cache_creation_input_tokens": 100}},
    })
    rec = parse_line(line)
    assert rec is not None and rec.cache_creation_1h_tokens == 0


def test_record_cache_version_bumped_for_the_new_field():
    """An old parse-cache entry lacks the 1h field and would replay as zero, so the
    cache version MUST change whenever UsageRecord grows a billing field."""
    assert parser._CLAUDE_CACHE_VERSION >= 2
