"""
backtest.py — rough historical sanity check for the momentum rotation engine.

Deliberately reuses the REAL decision modules (screener, entry_filter,
exit_engine, risk_engine, portfolio) fed with vectorized historical technicals
— so this tests the actual logic that runs in production, not a re-implemented
shadow of it. What it simplifies for tractability:
  - the war chest is tracked as plain cash earning a flat assumed annual yield
    (CASH_YIELD below), rather than real LIQUIDCASE price action;
  - no limit-order non-fills, flat Zerodha cost stack per trade;
  - universe/basket selection is monthly, using the same ADTV/MIN_MCAP rules
    as universe.py but computed offline from the same historical candles.

Compares the engine's net-of-tax, net-of-cost final value against a plain
monthly SIP into NIFTYBEES alone, same total contribution. Run this before
paper-trading; paper-trade before real capital.

Usage: python backtest.py
"""

import csv
import logging
import datetime as dt

import pandas as pd

import config
import data_fetcher as dfetch
import screener
import entry_filter
import exit_engine
import risk_engine
import portfolio as pf
from kite_auth import get_kite

log = logging.getLogger("backtest")

CASH_YIELD_ANNUAL = 0.06   # assumed liquid-fund rate for the idle war chest

# Zerodha cost stack (equity-delivery ETF, brokerage Rs 0)
STT, EXCH, SEBI, STAMP_BUY, GST, DP_SELL = 0.001, 0.0000297, 0.000001, 0.00015, 0.18, 15.34

def buy_cost(v):
    txn, sebi = v * EXCH, v * SEBI
    return v * STT + txn + sebi + v * STAMP_BUY + (txn + sebi) * GST

def sell_cost(v):
    txn, sebi = v * EXCH, v * SEBI
    return v * STT + txn + sebi + (txn + sebi) * GST + DP_SELL


def _rsi_series(close: pd.Series, window=14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / loss.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def load_histories(kite, candidates):
    data = {}
    for c in candidates:
        sym = c["symbol"]
        try:
            df = dfetch.get_history(kite, sym, days=365 * 9)
        except Exception as e:
            log.warning("Skipping %s (no history: %s)", sym, e)
            continue
        df = df.copy()
        df["sma200"] = df["close"].rolling(200).mean()
        df["sma50"] = df["close"].rolling(50).mean()
        df["rsi14"] = _rsi_series(df["close"])
        df["r1m"] = df["close"] / df["close"].shift(21) - 1
        df["r3m"] = df["close"] / df["close"].shift(63) - 1
        df["r6m"] = df["close"] / df["close"].shift(126) - 1
        df["r12m"] = df["close"] / df["close"].shift(252) - 1
        df["adtv20"] = (df["close"] * df["volume"]).rolling(config.LIQUIDITY_LOOKBACK_DAYS).mean()
        data[sym] = {"df": df, "category": c["category"]}
    return data


def select_basket_offline(data, date):
    scored = []
    for sym, d in data.items():
        if date not in d["df"].index:
            continue
        row = d["df"].loc[date]
        adtv = row["adtv20"]
        if pd.isna(adtv) or adtv < config.MIN_ADTV_INR:
            continue
        scored.append({"symbol": sym, "category": d["category"], "adtv": adtv})
    scored.sort(key=lambda x: x["adtv"], reverse=True)
    mcap = [s for s in scored if s["category"] == config.CATEGORY_MCAP]
    thematic = [s for s in scored if s["category"] == config.CATEGORY_THEMATIC]
    basket = mcap[:config.MIN_MCAP_IN_BASKET]
    pool = mcap[config.MIN_MCAP_IN_BASKET:] + thematic
    pool.sort(key=lambda x: x["adtv"], reverse=True)
    basket += pool[:config.BASKET_SIZE - len(basket)]
    return basket


def tech_at(data, sym, date):
    row = data[sym]["df"].loc[date]
    def n(x):
        return None if pd.isna(x) else float(x)
    return {"close": n(row["close"]), "sma200": n(row["sma200"]), "sma50": n(row["sma50"]),
           "rsi14": n(row["rsi14"]), "r1m": n(row["r1m"]), "r3m": n(row["r3m"]),
           "r6m": n(row["r6m"]), "r12m": n(row["r12m"])}


def run(data):
    # master calendar = NIFTYBEES dates if present, else the longest MCAP series
    mcap_syms = [s for s, d in data.items() if d["category"] == config.CATEGORY_MCAP]
    anchor = "NIFTYBEES" if "NIFTYBEES" in data else mcap_syms[0]
    dates = data[anchor]["df"].index
    dates = dates[dates >= dates[0] + pd.Timedelta(days=290)]  # need ~200 sessions warmup

    state = pf._default_state()
    state["cash"] = 0.0
    total_contributed = 0.0
    last_sip_month = None
    last_basket_month = None
    basket = []

    for date in dates:
        month_key = (date.year, date.month)

        # daily war-chest yield
        state["cash"] *= 1 + CASH_YIELD_ANNUAL / 252

        # monthly basket refresh
        if month_key != last_basket_month:
            basket = select_basket_offline(data, date)
            last_basket_month = month_key
            if not basket:
                continue

        tbs = {}
        for m in basket:
            sym = m["symbol"]
            if date not in data[sym]["df"].index:
                continue
            t = tech_at(data, sym, date)
            if t["close"] is not None:
                tbs[sym] = t

        ranked = screener.rank_basket(basket, tbs)

        # ---- exits (profit-gated) ----
        sells = exit_engine.decide_exits(state, ranked)
        for s in sells:
            sym, qty, px = s["symbol"], s["shares"], s["price"]
            proceeds = qty * px
            sc = sell_cost(proceeds)
            res = pf.remove_fifo(state, sym, qty, px)
            state["cash"] += proceeds - sc

        # ---- SIP (forced monthly, MCAP only) ----
        if month_key != last_sip_month and date.day >= config.SIP_DAY_OF_MONTH:
            mcap_eligible = entry_filter.mcap_candidates_for_sip(ranked)
            if not mcap_eligible:
                mcap_eligible = [r for r in ranked if r["category"] == config.CATEGORY_MCAP]
            sip_buys = (risk_engine.size_sip(mcap_eligible, config.MONTHLY_CONTRIBUTION)
                       if mcap_eligible else {})
            state["cash"] += config.MONTHLY_CONTRIBUTION
            total_contributed += config.MONTHLY_CONTRIBUTION
            for sym, value in sip_buys.items():
                bc = buy_cost(value)
                px = tbs[sym]["close"]
                qty_units = (value - bc) / px
                pf.add_lot(state, sym, qty_units, px)
                state["cash"] -= value
            last_sip_month = month_key

        # ---- tactical buys (daily, if eligible) ----
        total_value = state["cash"]
        for sym in tbs:
            total_value += pf.total_units(state, sym) * tbs[sym]["close"]
        current_weights = {sym: pf.total_units(state, sym) * tbs[sym]["close"] / total_value
                           for sym in tbs if pf.total_units(state, sym) > 0 and total_value > 0}
        tactical = entry_filter.top_tactical_candidates(ranked)
        tactical_buys = risk_engine.size_tactical(tactical, max(0.0, state["cash"]), current_weights)
        for sym, value in tactical_buys.items():
            if value > state["cash"]:
                continue
            bc = buy_cost(value)
            px = tbs[sym]["close"]
            qty_units = (value - bc) / px
            pf.add_lot(state, sym, qty_units, px)
            state["cash"] -= value

    # ---- liquidate everything at the end for a fair net number ----
    final_date = dates[-1]
    gross = 0.0
    stcg_end = ltcg_end = 0.0
    for sym in data:
        units = pf.total_units(state, sym)
        if units <= 0 or final_date not in data[sym]["df"].index:
            continue
        px = data[sym]["df"].loc[final_date, "close"]
        gross += units * px
        for lot in state["positions"][sym]["lots"]:
            gain = (px - lot["price"]) * lot["shares"]
            held = (final_date.date() - dt.date.fromisoformat(lot["date"])).days
            if held >= 365:
                ltcg_end += gain
            else:
                stcg_end += gain
    sell_costs = sell_cost(gross) if gross > 0 else 0.0
    ltcg_taxable = max(0, (state["realized_ltcg_fy"] + ltcg_end) - config.LTCG_ANNUAL_EXEMPT)
    stcg_total = max(0.0, state["realized_stcg_fy"] + stcg_end)
    # Note: real Indian tax law lets capital losses offset gains in the same FY
    # (set-off/carry-forward) — a forced liquidation that includes unrealized
    # losses on still-held positions could legitimately reduce the tax bill
    # below what's shown here. We floor at zero rather than model set-off, so
    # this errs conservative (slightly overstates the engine's tax drag) rather
    # than confusingly printing a negative "tax paid".
    tax = max(0.0, stcg_total * config.STCG_RATE + ltcg_taxable * config.LTCG_RATE)
    net_final = state["cash"] + gross - sell_costs - tax

    # ---- plain SIP benchmark: NIFTYBEES only, same contribution schedule ----
    anchor_df = data[anchor]["df"]
    sip_shares, sip_basis, sip_in, last_m = 0.0, 0.0, 0.0, None
    for date in dates:
        mk = (date.year, date.month)
        if mk != last_m:
            px = anchor_df.loc[date, "close"]
            bc = buy_cost(config.MONTHLY_CONTRIBUTION)
            sip_shares += (config.MONTHLY_CONTRIBUTION - bc) / px
            sip_basis += config.MONTHLY_CONTRIBUTION - bc
            sip_in += config.MONTHLY_CONTRIBUTION
            last_m = mk
    final_px = anchor_df.loc[final_date, "close"]
    sip_gross = sip_shares * final_px
    sip_tax = max(0, (sip_gross - sip_basis) - config.LTCG_ANNUAL_EXEMPT) * config.LTCG_RATE
    sip_net = sip_gross - sell_cost(sip_gross) - sip_tax

    years = (dates[-1] - dates[0]).days / 365.25
    print("\n================  ETF SHOP v2 BACKTEST  ================")
    print(f"Period            : {dates[0].date()} -> {dates[-1].date()} ({years:.1f}y)")
    print(f"Total contributed : Rs {total_contributed:,.0f}")
    print("-" * 54)
    print(f"ENGINE net value  : Rs {net_final:,.0f}  ({(net_final/total_contributed-1)*100:+.1f}% on cost)")
    print(f"        tax paid  : Rs {tax:,.0f}")
    print(f"SIP (anchor only) : Rs {sip_net:,.0f}  ({(sip_net/sip_in-1)*100:+.1f}% on cost)")
    print(f"        tax paid  : Rs {sip_tax:,.0f}")
    print("-" * 54)
    edge = (net_final / sip_net - 1) * 100
    print(f"ENGINE vs SIP     : {edge:+.2f}%   {'engine wins' if edge > 0 else 'SIP wins'}")
    print("=" * 54)
    print("Rough model: flat cost haircut, no LIQUIDCASE NAV drift, no fill\n"
         "slippage beyond the cost stack. Paper-trade before real capital.\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    kite = get_kite()
    with open(config.CANDIDATES_FILE) as f:
        candidates = list(csv.DictReader(f))
    data = load_histories(kite, candidates)
    if not data:
        raise SystemExit("No candidates resolved — check candidates.csv against "
                         "current NSE listings before backtesting.")
    run(data)
