"""
universe.py — turns candidates.csv into today's tradeable basket of
config.BASKET_SIZE ETFs, screened for liquidity.

Re-run monthly (config.UNIVERSE_REBALANCE). Each candidate is:
  1. validated against the broker's live instrument list (tickers get
     renamed/delisted/merged — don't trust a static CSV blindly);
  2. scored on 20-day average daily traded value (ADTV = avg volume * avg
     close), the standard liquidity proxy;
  3. dropped if ADTV < config.MIN_ADTV_INR — "sufficient liquidity" is an
     explicit floor, not just "whatever's left";
  4. the survivors are ranked by ADTV and the top BASKET_SIZE kept, with a
     guaranteed minimum count of MCAP members (so the forced SIP always has
     a core to buy even in a month where thematic ETFs dominate turnover).

Returns a list of dicts: {symbol, category, adtv}.
"""

import csv
import json
import os
import logging

import config
import data_fetcher as dfetch

log = logging.getLogger("universe")


def _load_candidates() -> list[dict]:
    with open(config.CANDIDATES_FILE) as f:
        return list(csv.DictReader(f))


def _resolve_and_score(kite, candidates: list[dict]) -> list[dict]:
    scored = []
    for c in candidates:
        sym = c["symbol"]
        try:
            df = dfetch.get_history(kite, sym, days=config.LIQUIDITY_LOOKBACK_DAYS + 10)
        except Exception as e:
            log.warning("Skipping %s — not resolvable/no history (%s). "
                       "Check candidates.csv against current NSE listings.", sym, e)
            continue
        tail = df.tail(config.LIQUIDITY_LOOKBACK_DAYS)
        if tail.empty:
            continue
        adtv = float((tail["close"] * tail["volume"]).mean())
        if adtv < config.MIN_ADTV_INR:
            log.info("Dropping %s — ADTV Rs %.0f below floor Rs %.0f",
                     sym, adtv, config.MIN_ADTV_INR)
            continue
        scored.append({"symbol": sym, "category": c["category"], "adtv": adtv})
    return scored


def select_basket(kite) -> list[dict]:
    candidates = _load_candidates()
    scored = _resolve_and_score(kite, candidates)
    if not scored:
        raise RuntimeError("No candidates passed the liquidity screen — "
                           "check candidates.csv and MIN_ADTV_INR.")

    scored.sort(key=lambda x: x["adtv"], reverse=True)

    mcap = [s for s in scored if s["category"] == config.CATEGORY_MCAP]
    thematic = [s for s in scored if s["category"] == config.CATEGORY_THEMATIC]

    basket: list[dict] = []
    # guarantee the MCAP floor first
    basket.extend(mcap[:config.MIN_MCAP_IN_BASKET])
    remaining_slots = config.BASKET_SIZE - len(basket)
    pool = mcap[config.MIN_MCAP_IN_BASKET:] + thematic
    pool.sort(key=lambda x: x["adtv"], reverse=True)
    basket.extend(pool[:remaining_slots])

    if len(mcap) < config.MIN_MCAP_IN_BASKET:
        log.warning("Only %d MCAP candidates passed the liquidity screen — "
                   "below the configured floor of %d. SIP core will be thin "
                   "this month.", len(mcap), config.MIN_MCAP_IN_BASKET)

    log.info("Basket selected (%d members): %s",
             len(basket), [b["symbol"] for b in basket])
    return basket


def save_basket(basket: list[dict]) -> None:
    os.makedirs(os.path.dirname(config.BASKET_FILE), exist_ok=True)
    with open(config.BASKET_FILE, "w") as f:
        json.dump(basket, f, indent=2)


def load_basket() -> list[dict] | None:
    if os.path.exists(config.BASKET_FILE):
        with open(config.BASKET_FILE) as f:
            return json.load(f)
    return None
