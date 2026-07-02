"""
profit_reduction.py — "don't wait too long on a position that's clearly rolling
over."

The mirror image of profit_extension.py. Because this system can never sell
at a loss, a position that's weakening has a closing window: if it keeps
fading, the profit on the table now might be gone — or worse, might round-trip
into a loss the system then has no way to exit. Locking in a smaller profit
sooner is the prudent move once the weakness is genuine, not just noise.

The four criteria (ALL must hold for any reduction at all — deliberately the
mirror of the extension's four):
  1. below its 50DMA                 — intermediate trend broken
  2. RSI14 below REDUCTION_RSI_CEILING (45) — genuine weakness, not just soft
  3. 1-month return negative         — the move has already reversed
  4. rank_overall in the bottom half of the WHOLE basket — genuinely weak
                                        across everything, not just relatively
                                        weak within a still-strong category

If any one fails: no reduction, the bar stays at the flat base (5%).
If all four hold: a continuous weakness score (0-1) sets where between
REDUCTION_MIN_PCT and REDUCTION_MAX_PCT the reduction lands — so the
effective bar ranges from 4.75% (barely qualifies) down to 2.75% (strongest
possible weakness reading).
"""

import config


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def is_eligible(member: dict) -> bool:
    tech = member["tech"]

    if config.REDUCTION_REQUIRE_BELOW_50DMA:
        if not (tech.get("sma50") and tech["close"] < tech["sma50"]):
            return False

    rsi = tech.get("rsi14")
    if rsi is None or rsi >= config.REDUCTION_RSI_CEILING:
        return False

    if config.REDUCTION_REQUIRE_NEGATIVE_1M:
        r1m = tech.get("r1m")
        if r1m is None or r1m >= 0:
            return False

    if config.REDUCTION_REQUIRE_BOTTOM_HALF_OVERALL:
        rank, total = member.get("rank_overall"), member.get("total_ranked")
        if not rank or not total or rank <= total / 2:
            return False

    return True


def _weakness_score(member: dict) -> float:
    tech = member["tech"]
    parts = []

    if tech.get("sma50"):
        below_pct = 1 - (tech["close"] / tech["sma50"])
        parts.append(_clamp01(below_pct / 0.10))

    rsi = tech.get("rsi14")
    if rsi is not None:
        parts.append(_clamp01((config.REDUCTION_RSI_CEILING - rsi) /
                             config.REDUCTION_RSI_CEILING))

    r1m = tech.get("r1m")
    if r1m is not None:
        parts.append(_clamp01(-r1m / 0.10))

    rank, total = member.get("rank_overall"), member.get("total_ranked")
    if rank and total and total > 1:
        parts.append(_clamp01((rank - 1) / (total - 1)))

    return sum(parts) / len(parts) if parts else 0.0


def required_profit_pct(member: dict) -> float:
    """The effective profit bar for this member today — base, or base-reduction
    if it qualifies as genuinely weakening."""
    base = config.BASE_PROFIT_BOOKING_PCT
    if not is_eligible(member):
        return base
    score = _weakness_score(member)
    reduction = (config.REDUCTION_MIN_PCT +
                score * (config.REDUCTION_MAX_PCT - config.REDUCTION_MIN_PCT))
    return base - reduction
