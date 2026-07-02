from __future__ import annotations
"""
indicators.py — plain-pandas technicals + the momentum composite score.
No TA-Lib. All functions are pure (take a DataFrame/Series, return numbers) so
they're trivially unit-testable without touching Kite.
"""

import pandas as pd

import config


def sma(series: pd.Series, window: int) -> float | None:
    if len(series) < window:
        return None
    return float(series.tail(window).mean())


def rsi(series: pd.Series, window: int = 14) -> float | None:
    if len(series) < window + 1:
        return None
    delta = series.diff().dropna()
    gain = delta.clip(lower=0).tail(window).mean()
    loss = (-delta.clip(upper=0)).tail(window).mean()
    if loss == 0:
        return 100.0
    rs = gain / loss
    return float(100 - 100 / (1 + rs))


def trailing_return(series: pd.Series, sessions: int) -> float | None:
    """Return over the last `sessions` trading sessions, e.g. 21 ~ 1 month."""
    if len(series) <= sessions:
        return None
    return float(series.iloc[-1] / series.iloc[-1 - sessions] - 1)


def technicals(df: pd.DataFrame) -> dict:
    """All indicators needed for ranking/filtering one symbol's daily OHLC frame."""
    close = df["close"]
    return {
        "close": float(close.iloc[-1]),
        "sma200": sma(close, 200),
        "sma50": sma(close, 50),
        "rsi14": rsi(close, 14),
        "r1m": trailing_return(close, 21),
        "r3m": trailing_return(close, 63),
        "r6m": trailing_return(close, 126),
        "r12m": trailing_return(close, 252),
    }


def momentum_score(tech: dict) -> float | None:
    """
    Composite momentum score = weighted blend of trailing returns
    (config.MOMENTUM_WEIGHTS). None if any required return is unavailable
    (e.g. a recently listed ETF without 12m of history) — such candidates
    simply can't be ranked yet and are excluded rather than guessed at.
    """
    total = 0.0
    for key, weight in config.MOMENTUM_WEIGHTS.items():
        r = tech.get(key)
        if r is None:
            return None
        total += weight * r
    return total


def above_200dma(tech: dict) -> bool:
    return bool(tech["sma200"] and tech["close"] > tech["sma200"])


def below_200dma(tech: dict) -> bool:
    return bool(tech["sma200"] and tech["close"] < tech["sma200"])


def above_50dma(tech: dict) -> bool:
    return bool(tech["sma50"] and tech["close"] > tech["sma50"])


def below_50dma(tech: dict) -> bool:
    return bool(tech["sma50"] and tech["close"] < tech["sma50"])
