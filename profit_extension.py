"""
profit_extension.py — "don't cut a winner that's still clearly working."

The exit engine's profit gate used to be a flat bar (BASE_PROFIT_BOOKING_PCT).
This module makes it dynamic: if a position shows ALL four signs of genuine,
ongoing strength, the bar is raised — scaled continuously by how strongly it
clears those signs, not just a pass/fail bump.

The four criteria (ALL must hold for any extension at all):
  1. still above its 50DMA           — intermediate trend intact
  2. RSI14 in [EXTENSION_RSI_FLOOR, EXTENSION_RSI_CEILING] — real strength,
                                        not just barely positive, not blown off
  3. 1-month return still positive   — the move hasn't already reversed
  4. rank_overall in the top half of the WHOLE basket — genuinely strong
                                        across everything, not just "least
                                        bad" within its own weak category

If any one fails: no extension, the bar stays at the flat base.
If all four hold: a continuous strength score (0-1) sets where between
EXTENSION_MIN_BONUS_PCT and EXTENSION_MAX_BONUS_PCT the bonus lands. The
score blends, equally:
  - how far above the 50DMA (capped at a 10% read)
  - how far above the RSI floor, as a fraction of the floor-to-ceiling band
  - the 1-month return itself (capped at a 10% read)
  - how high the overall rank is (rank 1 of N -> 1.0, last -> 0.0)
"""

import config


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def is_eligible(member: dict) -> bool:
    tech = member["tech"]

    if config.EXTENSION_REQUIRE_ABOVE_50DMA:
        if not (tech.get("sma50") and tech["close"] > tech["sma50"]):
            return False

    rsi = tech.get("rsi14")
    if rsi is None or not (config.EXTENSION_RSI_FLOOR <= rsi <= config.EXTENSION_RSI_CEILING):
        return False

    if config.EXTENSION_REQUIRE_POSITIVE_1M:
        r1m = tech.get("r1m")
        if r1m is None or r1m <= 0:
            return False

    if config.EXTENSION_REQUIRE_TOP_HALF_OVERALL:
        rank, total = member.get("rank_overall"), member.get("total_ranked")
        if not rank or not total or rank > total / 2:
            return False

    return True


def _strength_score(member: dict) -> float:
    tech = member["tech"]
    parts = []

    if tech.get("sma50"):
        above_pct = (tech["close"] / tech["sma50"]) - 1
        parts.append(_clamp01(above_pct / 0.10))

    rsi = tech.get("rsi14")
    if rsi is not None:
        band = config.EXTENSION_RSI_CEILING - config.EXTENSION_RSI_FLOOR
        parts.append(_clamp01((rsi - config.EXTENSION_RSI_FLOOR) / band) if band > 0 else 0.0)

    r1m = tech.get("r1m")
    if r1m is not None:
        parts.append(_clamp01(r1m / 0.10))

    rank, total = member.get("rank_overall"), member.get("total_ranked")
    if rank and total and total > 1:
        parts.append(_clamp01(1 - (rank - 1) / (total - 1)))

    return sum(parts) / len(parts) if parts else 0.0


def required_profit_pct(member: dict) -> float:
    """The effective profit bar for this member today — base, or base+bonus
    if it earns the extension."""
    base = config.BASE_PROFIT_BOOKING_PCT
    if not is_eligible(member):
        return base
    score = _strength_score(member)
    bonus = (config.EXTENSION_MIN_BONUS_PCT +
            score * (config.EXTENSION_MAX_BONUS_PCT - config.EXTENSION_MIN_BONUS_PCT))
    return base + bonus
