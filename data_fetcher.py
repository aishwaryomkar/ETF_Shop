"""
data_fetcher.py — rate-limited access to Kite historical candles and live quotes.

Kite enforces per-endpoint rate limits; we throttle conservatively. We also
cache the instrument token lookup so we don't refetch the whole instrument
dump every run.
"""

import time
import json
import os
import logging
import datetime as dt

import pandas as pd

import config

log = logging.getLogger("data")

_INSTRUMENT_CACHE = "state/instruments.json"
_last_hist_call = 0.0
_last_quote_call = 0.0


def _throttle(last: float, gap: float) -> float:
    wait = gap - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    return time.time()


def get_instrument_token(kite, tradingsymbol: str, exchange: str = config.EXCHANGE) -> int:
    """Resolve NSE tradingsymbol -> instrument_token, with a daily disk cache."""
    cache = {}
    if os.path.exists(_INSTRUMENT_CACHE):
        age = time.time() - os.path.getmtime(_INSTRUMENT_CACHE)
        if age < 24 * 3600:
            with open(_INSTRUMENT_CACHE) as f:
                cache = json.load(f)

    key = f"{exchange}:{tradingsymbol}"
    if key in cache:
        return cache[key]

    log.info("Fetching instrument dump for %s", exchange)
    instruments = kite.instruments(exchange)
    for inst in instruments:
        cache[f"{exchange}:{inst['tradingsymbol']}"] = inst["instrument_token"]
    os.makedirs("state", exist_ok=True)
    with open(_INSTRUMENT_CACHE, "w") as f:
        json.dump(cache, f)

    if key not in cache:
        raise ValueError(f"{key} not found in instrument list.")
    return cache[key]


def get_history(kite, tradingsymbol: str, days: int = 400) -> pd.DataFrame:
    """Daily OHLC candles for the last `days` calendar days as a DataFrame."""
    global _last_hist_call
    token = get_instrument_token(kite, tradingsymbol)
    to_date = dt.date.today()
    from_date = to_date - dt.timedelta(days=days)

    _last_hist_call = _throttle(_last_hist_call, config.HIST_RATE_LIMIT_S)
    candles = kite.historical_data(token, from_date, to_date, interval="day")
    df = pd.DataFrame(candles)
    if df.empty:
        raise RuntimeError(f"No history returned for {tradingsymbol}")
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df


def get_ltp(kite, tradingsymbol: str, exchange: str = config.EXCHANGE) -> float:
    """Last traded price."""
    global _last_quote_call
    _last_quote_call = _throttle(_last_quote_call, config.QUOTE_RATE_LIMIT_S)
    key = f"{exchange}:{tradingsymbol}"
    data = kite.ltp([key])
    return float(data[key]["last_price"])


def get_quote(kite, tradingsymbol: str, exchange: str = config.EXCHANGE) -> dict:
    global _last_quote_call
    _last_quote_call = _throttle(_last_quote_call, config.QUOTE_RATE_LIMIT_S)
    key = f"{exchange}:{tradingsymbol}"
    return kite.quote([key])[key]
