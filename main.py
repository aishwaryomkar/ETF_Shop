"""
main.py — daily orchestration. Run once per trading day after the open settles
(09:45 IST recommended — see deploy/etfshop.timer).

Flow: auth -> refresh basket (monthly) -> fetch technicals for every basket
      member -> strategy.decide -> order_engine.execute -> persist -> telemetry
"""

import sys
import logging

import config
import monitoring
import data_fetcher as dfetch
import indicators
import portfolio as pf
import strategy
import order_engine
import liquidity_buffer as lb
from kite_auth import get_kite

log = logging.getLogger("main")


def main() -> int:
    monitoring.setup_logging()
    log.info("=== ETF Shop (momentum rotation) run start (DRY_RUN=%s) ===", config.DRY_RUN)

    try:
        kite = get_kite()
    except Exception as e:
        log.error("Auth failed: %s", e)
        return 1

    state = pf.load()

    try:
        basket = strategy.refresh_basket_if_needed(kite, state)
    except Exception as e:
        log.error("Basket selection failed: %s", e)
        return 1

    tech_by_symbol = {}
    for member in basket:
        sym = member["symbol"]
        try:
            df = dfetch.get_history(kite, sym, days=400)
            tech_by_symbol[sym] = indicators.technicals(df)
        except Exception as e:
            log.warning("Skipping %s this run — history fetch failed (%s).", sym, e)

    # snapshot deployable liquidity BEFORE today's exits/buys (sizing reference)
    lq_ltp = dfetch.get_ltp(kite, config.LIQUID_ETF)
    deployable = lb.deployable_liquidity(state, lq_ltp)

    # current portfolio weights, for the risk engine's category caps
    total_value = state["cash"] + state["liquidbees_units"] * lq_ltp
    holdings_value = {}
    for sym, tech in tech_by_symbol.items():
        units = pf.total_units(state, sym)
        if units > 0:
            holdings_value[sym] = units * tech["close"]
            total_value += holdings_value[sym]
    current_weights = ({s: v / total_value for s, v in holdings_value.items()}
                       if total_value > 0 else {})

    decision = strategy.decide(state, basket, tech_by_symbol, current_weights, deployable)

    log.info("Ranked basket today:")
    for r in decision["ranked"]:
        log.info("  #%d  %-12s %-9s score=%.3f  rsi=%s",
                 r["rank_overall"], r["symbol"], r["category"], r["score"],
                 f"{r['tech']['rsi14']:.0f}" if r['tech'].get('rsi14') else "n/a")
    for s in decision["sells"]:
        log.info("SELL intent: %s", s)
    for sym, v in decision["sip_buys"].items():
        log.info("SIP intent: %s <- Rs %.0f", sym, v)
    for sym, v in decision["tactical_buys"].items():
        log.info("TACTICAL intent: %s <- Rs %.0f", sym, v)

    order_engine.execute(kite, state, decision, tech_by_symbol)
    pf.save(state)

    # telemetry
    total_after = state["cash"] + state["liquidbees_units"] * lq_ltp
    for sym, tech in tech_by_symbol.items():
        units = pf.total_units(state, sym)
        if units > 0:
            total_after += units * tech["close"]
    monitoring.record_equity(state, total_after, tech_by_symbol)
    for s in decision["sells"]:
        monitoring.record_trade("SELL", s["symbol"], s["shares"], s["price"], s["reason"])
    for sym, v in decision["sip_buys"].items():
        monitoring.record_trade("SIP_BUY", sym, None, v, "monthly SIP")
    for sym, v in decision["tactical_buys"].items():
        monitoring.record_trade("TACTICAL_BUY", sym, None, v, "tactical")

    log.info("Run complete. Total portfolio value (incl. war chest): Rs %.0f", total_after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
