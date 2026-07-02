from __future__ import annotations
"""
exit_engine.py — decides what (if anything) to sell today.

Two gates, BOTH required, in this order:
  1. SIGNAL  — does momentum/trend say this holding should be reduced?
               (rank fell into the bottom half of its category, and/or price
               broke below its 200DMA — "severe" if trend-broken, else "mild")
  2. PROFIT  — is the position sitting at or above its REQUIRED profit bar?
               The bar is dynamic, moving in BOTH directions (see profit_bar.py,
               profit_extension.py, profit_reduction.py): a flat
               BASE_PROFIT_BOOKING_PCT (5%) by default, raised toward 10% if
               the position shows genuine ongoing STRENGTH (still above its
               50DMA, healthy RSI, positive 1-month return, top-half of the
               whole basket), or lowered toward 2.75% if it shows genuine
               ongoing WEAKNESS (the mirror conditions) — lock in whatever
               profit is on the table sooner before a fading position's
               window closes, since this system can never sell at a loss if
               it lets the gain erode away entirely.

Gate 2 is absolute. If a signal fires on a position that's below its required
bar — whether that bar is the flat 5% or an extended 8%, doesn't matter —
nothing happens. It is simply held. This system never sells at a loss, full
stop, for either category. Category only changes what happens once the
profit gate clears:
  - THEMATIC -> sell the entire position (lock in the gain, theme is done)
  - MCAP     -> trim 10% (mild) or 20% (severe), never below the structural
               floor (peak_units * MCAP_MIN_RETAINED_FRACTION), and at most
               once every MCAP_TRIM_COOLDOWN_DAYS per symbol

Returns a list of {symbol, category, shares, reason} sell intents.
"""

import logging

import config
import indicators
import portfolio as pf
import profit_bar

log = logging.getLogger("exit")


def _signal_severity(ranked_member: dict) -> str | None:
    """'severe' | 'mild' | None, based on rank-in-category and trend."""
    rank = ranked_member["rank_in_category"]
    size = ranked_member["category_size"]
    rank_bad = rank > size * (1 - config.EXIT_RANK_CUTOFF_FRACTION)
    trend_broken = (config.EXIT_REQUIRE_BELOW_200DMA and
                    indicators.below_200dma(ranked_member["tech"]))

    if trend_broken:
        return "severe"
    if rank_bad:
        return "mild"
    return None


def decide_exits(state: dict, ranked: list[dict]) -> list[dict]:
    intents = []

    for member in ranked:
        sym = member["symbol"]
        units = pf.total_units(state, sym)
        if units <= 0:
            continue

        severity = _signal_severity(member)
        if severity is None:
            continue

        cost = pf.avg_cost(state, sym)
        ltp = member["tech"]["close"]
        if cost <= 0:
            continue
        unrealized_pct = (ltp - cost) / cost
        required = profit_bar.required_profit_pct(member)

        if unrealized_pct < required:
            tag = ""
            if required > config.BASE_PROFIT_BOOKING_PCT:
                tag = " [extended — still showing strength]"
            elif required < config.BASE_PROFIT_BOOKING_PCT:
                tag = " [reduced — showing weakness, locking in sooner]"
            log.info("%s: %s signal fired but position is at %.1f%% "
                     "(< %.1f%%%s required) — holding, no stop-loss by design.",
                     sym, severity, unrealized_pct * 100, required * 100, tag)
            continue

        if member["category"] == config.CATEGORY_THEMATIC:
            intents.append({"symbol": sym, "category": "THEMATIC",
                            "shares": units, "price": ltp,
                            "reason": f"{severity} signal, +{unrealized_pct*100:.1f}% "
                                      f"profit — full exit"})
            continue

        # MCAP: cooldown + floor-respecting partial trim
        if not pf.can_trim_today(state, sym):
            log.info("%s: MCAP trim signal (%s) but cooldown active — skipping.",
                     sym, severity)
            continue

        frac = (config.SELL_FRACTION_MCAP_SEVERE if severity == "severe"
                else config.SELL_FRACTION_MCAP_MILD)
        floor = pf.mcap_floor_units(state, sym)
        sellable = max(0.0, units - floor)
        shares = min(units * frac, sellable)

        if shares < 1e-6:
            log.info("%s: MCAP trim signal (%s) but already at the %.0f%% "
                     "floor — holding.", sym, severity,
                     config.MCAP_MIN_RETAINED_FRACTION * 100)
            continue

        intents.append({"symbol": sym, "category": "MCAP", "shares": shares,
                        "price": ltp,
                        "reason": f"{severity} signal, +{unrealized_pct*100:.1f}% "
                                  f"profit — trim {frac*100:.0f}%"})
        pf.record_trim(state, sym)

    return intents
