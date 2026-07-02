from __future__ import annotations
"""
portfolio.py — persisted multi-symbol state.

Each symbol's position is FIFO lots (for tax classification on any future
sell) plus `peak_units` — the most units that symbol ever held — which is
what enforces the MCAP "never fully exit" floor: a MCAP holding can never be
trimmed below MCAP_MIN_RETAINED_FRACTION * peak_units, no matter how many
mild/severe signals fire over the years.

This is LOCAL bookkeeping, not the broker's source of truth — reconcile
against kite.holdings() periodically.
"""

import os
import json
import datetime as dt
import logging

import config

log = logging.getLogger("portfolio")


def _today() -> str:
    return dt.date.today().isoformat()


def _default_state() -> dict:
    return {
        "positions": {},            # symbol -> {lots: [...], peak_units: float}
        "cash": 0.0,
        "liquidbees_units": 0.0,
        "last_sip_month": None,
        "last_basket_month": None,
        "mcap_trim_last_day": {},   # symbol -> isoformat date of last trim
        "orders_today": 0,
        "orders_today_date": None,
        "realized_stcg_fy": 0.0,
        "realized_ltcg_fy": 0.0,
    }


def load() -> dict:
    if os.path.exists(config.PORTFOLIO_FILE):
        with open(config.PORTFOLIO_FILE) as f:
            state = json.load(f)
        if state.get("orders_today_date") != _today():
            state["orders_today"] = 0
            state["orders_today_date"] = _today()
        return state
    return _default_state()


def save(state: dict) -> None:
    os.makedirs(os.path.dirname(config.PORTFOLIO_FILE), exist_ok=True)
    with open(config.PORTFOLIO_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _pos(state: dict, symbol: str) -> dict:
    return state["positions"].setdefault(symbol, {"lots": [], "peak_units": 0.0})


def total_units(state: dict, symbol: str) -> float:
    return sum(l["shares"] for l in state["positions"].get(symbol, {}).get("lots", []))


def avg_cost(state: dict, symbol: str) -> float:
    lots = state["positions"].get(symbol, {}).get("lots", [])
    sh = sum(l["shares"] for l in lots)
    if sh <= 0:
        return 0.0
    return sum(l["shares"] * l["price"] for l in lots) / sh


def add_lot(state: dict, symbol: str, shares: float, price: float) -> None:
    pos = _pos(state, symbol)
    pos["lots"].append({"shares": shares, "price": price, "date": _today()})
    pos["peak_units"] = max(pos["peak_units"], total_units(state, symbol))


def remove_fifo(state: dict, symbol: str, shares_to_sell: float, sell_price: float) -> dict:
    """FIFO removal. Returns {'stcg', 'ltcg', 'shares_sold'}."""
    pos = _pos(state, symbol)
    remaining = shares_to_sell
    stcg = ltcg = sold = 0.0
    today = dt.date.today()

    while remaining > 1e-9 and pos["lots"]:
        lot = pos["lots"][0]
        take = min(lot["shares"], remaining)
        gain = (sell_price - lot["price"]) * take
        held_days = (today - dt.date.fromisoformat(lot["date"])).days
        if held_days >= 365:
            ltcg += gain
        else:
            stcg += gain
        lot["shares"] -= take
        remaining -= take
        sold += take
        if lot["shares"] <= 1e-9:
            pos["lots"].pop(0)

    state["realized_stcg_fy"] += stcg
    state["realized_ltcg_fy"] += ltcg
    return {"stcg": stcg, "ltcg": ltcg, "shares_sold": sold}


def mcap_floor_units(state: dict, symbol: str) -> float:
    """The minimum units a MCAP holding must always retain."""
    peak = state["positions"].get(symbol, {}).get("peak_units", 0.0)
    return peak * config.MCAP_MIN_RETAINED_FRACTION


def can_trim_today(state: dict, symbol: str) -> bool:
    """Cooldown gate — at most one MCAP trim per MCAP_TRIM_COOLDOWN_DAYS."""
    last = state["mcap_trim_last_day"].get(symbol)
    if not last:
        return True
    days_since = (dt.date.today() - dt.date.fromisoformat(last)).days
    return days_since >= config.MCAP_TRIM_COOLDOWN_DAYS


def record_trim(state: dict, symbol: str) -> None:
    state["mcap_trim_last_day"][symbol] = _today()
