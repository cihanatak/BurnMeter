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

import pytest

from burnmeter.analytics import pricing_coverage
from burnmeter.pricing import (
    PRICES,
    PRICES_VERIFIED_AT,
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
    "gpt-5.5":                    (5.00, 30.00, 0.50, 5.00),
    "gpt-5.4":                    (2.50, 15.00, 0.25, 2.50),
    "gpt-5.4-mini":               (0.75, 4.50, 0.075, 0.75),
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
    """gpt-5.4-mini is a mini model, not the 5.4 flagship; same for codex."""
    assert resolve_price("gpt-5.4-mini")[0] == "gpt-5-mini"
    assert resolve_price("gpt-5.3-codex")[0] == "gpt-5-codex"


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
