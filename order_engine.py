from __future__ import annotations
"""
order_engine.py — the ONLY module that calls kite.place_order.

Execution order each run (matters): SELLS -> SWEEP -> SIP BUYS -> TACTICAL BUYS.
Selling first means a same-day profit-booked exit becomes available war chest
for buys later in the same run — but a same-day buy is still sized off
liquidity computed BEFORE that day's exit, per config.ROUTE_SELL_PROCEEDS_TO_WARCHEST,
to honor "proceeds get stored, not instantly redeployed." In practice this
means: compute deployable_liquidity once at the start of the run (before
exits), size everything off that snapshot, execute sells, sweep, then buys.

All orders: product=CNC, order_type=LIMIT, exchange=NSE. Never MARKET.
DRY_RUN logs intents without trading. Per-day circuit breaker caps order count.
"""

import logging

import config
import data_fetcher as dfetch
import portfolio as pf
import liquidity_buffer as lb

log = logging.getLogger("orders")


def _limit_price(ltp: float, side: str) -> float:
    if side == "BUY":
        return round(ltp * (1 + config.LIMIT_BUFFER_PCT), 2)
    return round(ltp * (1 - config.LIMIT_BUFFER_PCT), 2)


def _gap_guard(ltp: float, last_close: float) -> bool:
    if last_close <= 0:
        return True
    move = abs(ltp - last_close) / last_close
    if move > config.MAX_SLIPPAGE_PCT:
        log.warning("Gap guard tripped: LTP %.2f vs last close %.2f (%.2f%%) — skipping.",
                   ltp, last_close, move * 100)
        return False
    return True


def _can_order(state: dict) -> bool:
    if state["orders_today"] >= config.MAX_ORDERS_PER_DAY:
        log.warning("Daily order cap (%d) reached — circuit breaker.",
                   config.MAX_ORDERS_PER_DAY)
        return False
    return True


def _place(kite, symbol: str, side: str, qty: int, limit: float) -> str | None:
    if qty < 1:
        return None
    if config.DRY_RUN:
        log.info("[DRY_RUN] %s %d %s @ %.2f (CNC LIMIT)", side, qty, symbol, limit)
        return "DRY_RUN"
    order_id = kite.place_order(
        variety=kite.VARIETY_REGULAR, exchange=config.EXCHANGE,
        tradingsymbol=symbol, transaction_type=side, quantity=qty,
        product=kite.PRODUCT_CNC, order_type=kite.ORDER_TYPE_LIMIT,
        price=limit, validity=kite.VALIDITY_DAY,
    )
    log.info("Placed %s %d %s @ %.2f -> order_id %s", side, qty, symbol, limit, order_id)
    return order_id


def _last_close_map(tech_by_symbol: dict) -> dict:
    return {sym: t["close"] for sym, t in tech_by_symbol.items()}


def execute_sells(kite, state: dict, sells: list[dict], tech_by_symbol: dict) -> None:
    for s in sells:
        if not _can_order(state):
            break
        sym = s["symbol"]
        ltp = dfetch.get_ltp(kite, sym)
        if not _gap_guard(ltp, tech_by_symbol.get(sym, {}).get("close", ltp)):
            continue
        limit = _limit_price(ltp, "SELL")
        qty = int(s["shares"])
        if qty < 1:
            continue
        oid = _place(kite, sym, "SELL", qty, limit)
        if oid:
            realized = pf.remove_fifo(state, sym, qty, limit)
            proceeds = qty * limit
            state["cash"] += proceeds
            state["orders_today"] += 1
            log.info("SELL %d %s (%s). Proceeds Rs %.0f -> cash/war chest. "
                     "Realized STCG Rs %.0f / LTCG Rs %.0f",
                     qty, sym, s["reason"], proceeds, realized["stcg"], realized["ltcg"])


def sweep_and_get_liquidbees_ltp(kite, state: dict) -> float:
    lq_ltp = dfetch.get_ltp(kite, config.LIQUID_ETF)
    if config.ROUTE_SELL_PROCEEDS_TO_WARCHEST:
        lb.sweep_idle_cash(kite, state, lq_ltp)
    return lq_ltp


def execute_buys(kite, state: dict, buys: dict, label: str,
                 liquidbees_ltp: float) -> None:
    for sym, value in buys.items():
        if not _can_order(state):
            break
        if value < config.MIN_ORDER_VALUE:
            continue
        available = lb.draw_funds(state, value, liquidbees_ltp)
        spend = min(value, available)
        if spend < config.MIN_ORDER_VALUE:
            log.info("%s buy for %s skipped — insufficient funds even after "
                     "drawing the war chest (needed Rs %.0f, had Rs %.0f).",
                     label, sym, value, available)
            continue
        ltp = dfetch.get_ltp(kite, sym)
        limit = _limit_price(ltp, "BUY")
        qty = int(spend // limit)
        if qty < 1:
            continue
        oid = _place(kite, sym, "BUY", qty, limit)
        if oid:
            pf.add_lot(state, sym, qty, limit)
            state["cash"] -= qty * limit
            state["orders_today"] += 1
            log.info("%s BUY %d %s @ %.2f", label, qty, sym, limit)


def execute(kite, state: dict, decision: dict, tech_by_symbol: dict) -> None:
    execute_sells(kite, state, decision["sells"], tech_by_symbol)
    lq_ltp = sweep_and_get_liquidbees_ltp(kite, state)
    execute_buys(kite, state, decision["sip_buys"], "SIP", lq_ltp)
    execute_buys(kite, state, decision["tactical_buys"], "TACTICAL", lq_ltp)
