from __future__ import annotations
"""
strategy.py — the brain. One call (`decide`) per day, returns a full list of
intended actions for order_engine.py to execute (or log, in DRY_RUN).

Sequence each run:
  1. Refresh the basket if it's a new calendar month (universe.select_basket).
  2. Fetch technicals for every basket member, rank by momentum (screener).
  3. EXITS first — profit-gated, category-asymmetric (exit_engine). Proceeds
     land in cash and get swept to LIQUIDCASE before any buy is sized, so a
     same-day exit funds future buys, never the same day's own buys.
  4. SIP — once per calendar month, force-deploy into eligible MCAP members,
     weighted by momentum (risk_engine.size_sip). This ALWAYS fires if it's
     the day, regardless of any entry filter — that's the forced-participation
     guarantee carried over from the SIP-vs-timing analysis.
  5. Tactical buys — daily, ONLY if today's eligible candidates pass the
     entry filter, sized off accumulated liquidity (risk_engine.size_tactical).
"""

import datetime as dt
import logging

import config
import universe
import screener
import entry_filter
import exit_engine
import risk_engine

log = logging.getLogger("strategy")


def _this_month() -> str:
    return dt.date.today().strftime("%Y-%m")


def refresh_basket_if_needed(kite, state: dict) -> list[dict]:
    if state.get("last_basket_month") != _this_month():
        basket = universe.select_basket(kite)
        universe.save_basket(basket)
        state["last_basket_month"] = _this_month()
        return basket
    loaded = universe.load_basket()
    if loaded:
        return loaded
    basket = universe.select_basket(kite)
    universe.save_basket(basket)
    return basket


def decide(state: dict, basket: list[dict], tech_by_symbol: dict,
          current_weights: dict, deployable_liquidity: float) -> dict:
    """
    Returns:
      {"sells": [...], "sip_buys": {symbol: inr}, "tactical_buys": {symbol: inr},
       "ranked": [...]}
    """
    ranked = screener.rank_basket(basket, tech_by_symbol)

    # ---- 1. exits (profit-gated, runs before any buy is sized) ----
    sells = exit_engine.decide_exits(state, ranked)

    # ---- 2. SIP (forced, monthly, MCAP-only) ----
    sip_buys = {}
    today = dt.date.today()
    if (state.get("last_sip_month") != _this_month()
            and today.day >= config.SIP_DAY_OF_MONTH):
        mcap_eligible = entry_filter.mcap_candidates_for_sip(ranked)
        if not mcap_eligible:
            mcap_eligible = [r for r in ranked if r["category"] == config.CATEGORY_MCAP]
            log.warning("No MCAP candidate passed the entry filter this month — "
                       "SIP falling back to the raw MCAP ranking (forced "
                       "participation overrides the entry filter, never the SIP).")
        sip_buys = risk_engine.size_sip(mcap_eligible, config.MONTHLY_CONTRIBUTION)
        state["last_sip_month"] = _this_month()

    # ---- 3. tactical buys (daily, only if criteria met) ----
    tactical_candidates = entry_filter.top_tactical_candidates(ranked)
    tactical_buys = risk_engine.size_tactical(
        tactical_candidates, deployable_liquidity, current_weights)

    return {"sells": sells, "sip_buys": sip_buys, "tactical_buys": tactical_buys,
           "ranked": ranked}
