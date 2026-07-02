from __future__ import annotations
"""
risk_engine.py — turns "these candidates are eligible to buy" into actual
rupee tranches, sized off ACCUMULATED LIQUIDITY (cash + LIQUIDCASE value),
not a fixed amount. A flush war chest deploys more; a thin one barely trades.

Two separate sizing paths:
  - size_sip(): the fixed monthly contribution, split across eligible MCAP
    members weighted by momentum score (with a floor so it's never 100% into
    one name), funded directly by new money — NOT by the war chest.
  - size_tactical(): daily discretionary buys, funded by deployable liquidity
    (cash + LIQUIDCASE), split among the top-K eligible candidates (any
    category) weighted by score, capped per-symbol by category and by
    current portfolio weight so nothing gets oversized.
"""

import logging

import config

log = logging.getLogger("risk")


def _softmax_weights(scores: list[float]) -> list[float]:
    """Score-proportional weights (linear, not exponential — momentum scores
    can be negative, so a real softmax would behave oddly near zero)."""
    shifted = [s - min(scores) + 1e-6 for s in scores]  # all positive
    total = sum(shifted)
    return [s / total for s in shifted]


def size_sip(mcap_candidates: list[dict], monthly_amount: float) -> dict[str, float]:
    """{symbol: inr_amount} for the forced monthly SIP, MCAP-only."""
    if not mcap_candidates:
        return {}
    scores = [c["score"] for c in mcap_candidates]
    weights = _softmax_weights(scores)
    n = len(mcap_candidates)
    floor = config.SIP_MIN_WEIGHT_PER_MCAP
    # blend toward the floor so no single name is starved, then renormalize
    blended = [(1 - floor * n) * w + floor for w in weights] if floor * n < 1 else \
              [1 / n] * n
    s = sum(blended)
    blended = [b / s for b in blended]
    return {c["symbol"]: monthly_amount * w for c, w in zip(mcap_candidates, blended)}


def size_tactical(eligible: list[dict], deployable_liquidity: float,
                  current_weights: dict[str, float]) -> dict[str, float]:
    """
    {symbol: inr_amount} for today's tactical buys.
    current_weights: {symbol: current_position_value / total_portfolio_value}
    used to enforce per-symbol caps so we don't keep buying a name that's
    already at its category's max weight.
    """
    if not eligible or deployable_liquidity <= 0:
        return {}

    budget = deployable_liquidity * config.DAILY_TACTICAL_DEPLOY_FRACTION
    scores = [c["score"] for c in eligible]
    weights = _softmax_weights(scores)

    raw = {c["symbol"]: budget * w for c, w in zip(eligible, weights)}

    # cap by category weight ceiling
    capped = {}
    for c in eligible:
        sym = c["symbol"]
        cap = (config.MAX_WEIGHT_MCAP if c["category"] == config.CATEGORY_MCAP
               else config.MAX_WEIGHT_THEMATIC)
        room = max(0.0, cap - current_weights.get(sym, 0.0))
        # room is a *weight* headroom; convert to an INR ceiling using total
        # portfolio value implied by current_weights + deployable_liquidity
        # as a simple proxy (good enough for a sizing cap, not a precise NAV)
        portfolio_value_proxy = deployable_liquidity / max(
            1e-6, 1 - sum(current_weights.values()))
        inr_cap = room * portfolio_value_proxy
        amt = min(raw[sym], inr_cap)
        if amt >= config.MIN_ORDER_VALUE:
            capped[sym] = amt
        else:
            log.info("Tactical buy for %s skipped — below min order value "
                     "after category-weight cap (room: Rs %.0f).", sym, inr_cap)
    return capped
