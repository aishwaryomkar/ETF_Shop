from __future__ import annotations
"""
screener.py — ranks the basket by momentum_score, both overall and per
category (MCAP / THEMATIC), since the SIP only ever looks at the MCAP
ranking and the entry/exit logic needs category-relative rank.

Input: {symbol: tech_dict} where tech_dict = indicators.technicals(df)
Output: a list of {symbol, category, score, rank_overall, rank_in_category,
                   category_size} sorted best-to-worst overall.
Candidates with score=None (insufficient history) are excluded entirely.
"""

import logging

import indicators

log = logging.getLogger("screener")


def rank_basket(basket: list[dict], tech_by_symbol: dict[str, dict]) -> list[dict]:
    scored = []
    for member in basket:
        sym = member["symbol"]
        tech = tech_by_symbol.get(sym)
        if not tech:
            log.warning("No technicals for %s — excluding from ranking.", sym)
            continue
        score = indicators.momentum_score(tech)
        if score is None:
            log.info("%s has insufficient history for a momentum score — excluding.", sym)
            continue
        scored.append({"symbol": sym, "category": member["category"],
                       "score": score, "tech": tech})

    scored.sort(key=lambda x: x["score"], reverse=True)
    for i, s in enumerate(scored):
        s["rank_overall"] = i + 1
        s["total_ranked"] = len(scored)

    for cat in {s["category"] for s in scored}:
        cat_members = [s for s in scored if s["category"] == cat]
        cat_members.sort(key=lambda x: x["score"], reverse=True)
        for i, s in enumerate(cat_members):
            s["rank_in_category"] = i + 1
            s["category_size"] = len(cat_members)

    return scored
