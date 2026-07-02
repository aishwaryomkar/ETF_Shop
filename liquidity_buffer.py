"""
liquidity_buffer.py — the war chest, made real.

Idle cash above CASH_BUFFER_FLOOR gets swept into LIQUIDCASE so it earns the
overnight rate while waiting for a signal instead of sitting dead. Profit
booked on an exit lands here too (cash -> swept to LIQUIDCASE), per
config.ROUTE_SELL_PROCEEDS_TO_WARCHEST — it doesn't get spent same-day.

deployable_liquidity = cash + (liquidbees_units * liquidbees_ltp)
This is the number risk_engine sizes every buy against — a flush war chest
buys bigger, a thin one barely trades.
"""

import logging

import config
import data_fetcher as dfetch

log = logging.getLogger("liquidity")


def deployable_liquidity(state: dict, liquidbees_ltp: float) -> float:
    return state["cash"] + state["liquidbees_units"] * liquidbees_ltp


def sweep_idle_cash(kite, state: dict, liquidbees_ltp: float) -> float:
    """Move cash above the floor into LIQUIDCASE. Returns INR swept."""
    excess = state["cash"] - config.CASH_BUFFER_FLOOR
    if excess < config.MIN_ORDER_VALUE:
        return 0.0
    units = excess / liquidbees_ltp
    state["cash"] -= excess
    state["liquidbees_units"] += units
    log.info("Swept Rs %.0f cash -> %.4f LIQUIDCASE units (war chest now Rs %.0f)",
             excess, units, deployable_liquidity(state, liquidbees_ltp))
    return excess


def draw_funds(state: dict, value_needed: float, liquidbees_ltp: float) -> float:
    """
    Ensure `value_needed` INR is available as cash, redeeming LIQUIDCASE units
    if the raw cash balance is short. Returns the amount actually made
    available (may be less than requested if the war chest is insufficient).
    """
    if state["cash"] >= value_needed:
        return value_needed
    shortfall = value_needed - state["cash"]
    units_needed = shortfall / liquidbees_ltp
    units_to_sell = min(units_needed, state["liquidbees_units"])
    redeemed = units_to_sell * liquidbees_ltp
    state["liquidbees_units"] -= units_to_sell
    state["cash"] += redeemed
    if units_to_sell > 0:
        log.info("Redeemed %.4f LIQUIDCASE units (Rs %.0f) to fund a buy.",
                 units_to_sell, redeemed)
    return min(value_needed, state["cash"])
