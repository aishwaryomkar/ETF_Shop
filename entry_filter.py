"""
entry_filter.py — decides which ranked candidates are ELIGIBLE to be bought
today. Ranking alone isn't enough: the #1 momentum name could be in a
blow-off top (RSI too hot) or in a longer-term downtrend despite a recent pop
(below 200DMA) — momentum strategies that skip the trend/overbought check
chase exactly the names about to roll over.

Gate, applied to every ranked member:
  - price > 200DMA                          (long-term uptrend confirmed)
  - ENTRY_MIN_RSI <= RSI14 <= ENTRY_MAX_RSI (not a falling knife, not blown off)

Returns the ranked list filtered down to eligible members, still sorted by
score, with an `eligible: True` tag (kept for visibility in logs/telemetry —
callers should use the returned list, which already excludes ineligible ones).
"""

import logging

import config
import indicators

log = logging.getLogger("entry_filter")


def filter_eligible(ranked: list[dict]) -> list[dict]:
    eligible = []
    for r in ranked:
        tech = r["tech"]
        if config.ENTRY_REQUIRE_ABOVE_200DMA and not indicators.above_200dma(tech):
            continue
        rsi = tech.get("rsi14")
        if rsi is not None:
            if rsi > config.ENTRY_MAX_RSI:
                continue
            if rsi < config.ENTRY_MIN_RSI:
                continue
        eligible.append(r)
    return eligible


def top_tactical_candidates(ranked: list[dict], k: int = None) -> list[dict]:
    """Top-K eligible candidates (any category) for daily tactical buys."""
    k = k or config.TACTICAL_TOP_K
    return filter_eligible(ranked)[:k]


def mcap_candidates_for_sip(ranked: list[dict]) -> list[dict]:
    """All eligible MCAP members — the SIP weights across these, not just #1,
    so the core sleeve stays diversified even while tilting to the leader."""
    eligible = filter_eligible(ranked)
    return [r for r in eligible if r["category"] == config.CATEGORY_MCAP]
