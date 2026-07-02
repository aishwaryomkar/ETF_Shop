"""
profit_bar.py — the single dynamic profit threshold exit_engine actually uses.

Combines profit_extension.py (raises the bar for genuine strength) and
profit_reduction.py (lowers it for genuine weakness). Their eligibility
criteria are structurally exclusive by construction — extension requires
top-half-overall/above-50DMA/RSI 50-80/positive 1m, reduction requires the
opposite on every count — so in practice at most one ever fires. Extension is
checked first only as a defensive ordering choice, not because overlap is
expected.
"""

import config
import profit_extension
import profit_reduction


def required_profit_pct(member: dict) -> float:
    if profit_extension.is_eligible(member):
        return profit_extension.required_profit_pct(member)
    if profit_reduction.is_eligible(member):
        return profit_reduction.required_profit_pct(member)
    return config.BASE_PROFIT_BOOKING_PCT
