"""
monitoring.py — logging + appended telemetry.

Each run appends ONE row to the telemetry CSVs (not a fresh file), so after a
month of cron runs you have an actual curve to look at, not just today.
"""

import os
import csv
import logging
import datetime as dt

import config


def setup_logging() -> None:
    os.makedirs(config.RUN_LOG_DIR, exist_ok=True)
    logfile = os.path.join(config.RUN_LOG_DIR,
                           f"run_{dt.date.today().isoformat()}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-10s %(levelname)-7s %(message)s",
        handlers=[logging.FileHandler(logfile), logging.StreamHandler()],
    )


def _append_row(path: str, header: list[str], row: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exists = os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(header)
        w.writerow(row)


def record_equity(state: dict, total_value: float, tech_by_symbol: dict) -> None:
    holdings_summary = ";".join(
        f"{sym}:{round(pf_units,4)}" for sym, pf_units in
        [(s, _units(state, s)) for s in tech_by_symbol]
        if pf_units > 0
    )
    _append_row(
        config.TELEMETRY_FILE,
        ["date", "total_value", "cash", "liquidbees_units", "holdings"],
        [dt.date.today().isoformat(), round(total_value, 2),
         round(state["cash"], 2), round(state["liquidbees_units"], 4),
         holdings_summary],
    )


def _units(state: dict, symbol: str) -> float:
    return sum(l["shares"] for l in state["positions"].get(symbol, {}).get("lots", []))


def record_trade(action: str, symbol: str, qty, value, reason: str) -> None:
    _append_row(
        config.TRADES_FILE,
        ["datetime", "action", "symbol", "qty", "value", "reason"],
        [dt.datetime.now().isoformat(timespec="seconds"), action, symbol,
         qty, round(value, 2) if value is not None else "", reason],
    )
