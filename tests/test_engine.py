"""
tests/test_engine.py — unit tests for the momentum rotation engine.
Run: pytest tests/ -v
"""

import os
import sys
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import indicators
import portfolio as pf
import risk_engine
import exit_engine
import entry_filter
import screener


def tech(close, sma200=None, sma50=None, rsi14=50, r1m=0.0, r3m=0.0, r6m=0.0, r12m=0.0):
    return {"close": close, "sma200": sma200, "sma50": sma50, "rsi14": rsi14,
           "r1m": r1m, "r3m": r3m, "r6m": r6m, "r12m": r12m}


# ---------------- momentum score ----------------

def test_momentum_score_blend():
    t = tech(100, r1m=0.10, r3m=0.10, r6m=0.10, r12m=0.10)
    assert abs(indicators.momentum_score(t) - 0.10) < 1e-9


def test_momentum_score_none_if_missing_history():
    t = tech(100, r1m=0.10, r3m=0.10, r6m=0.10, r12m=None)
    assert indicators.momentum_score(t) is None


def test_above_below_200dma():
    assert indicators.above_200dma(tech(110, sma200=100)) is True
    assert indicators.below_200dma(tech(90, sma200=100)) is True
    assert indicators.above_200dma(tech(90, sma200=100)) is False


# ---------------- screener ranking ----------------

def test_rank_basket_orders_by_score_and_excludes_none():
    basket = [{"symbol": "A", "category": "MCAP"}, {"symbol": "B", "category": "MCAP"},
             {"symbol": "C", "category": "THEMATIC"}]
    tbs = {
        "A": tech(100, sma200=90, r1m=.05, r3m=.05, r6m=.05, r12m=.05),
        "B": tech(100, sma200=90, r1m=.20, r3m=.20, r6m=.20, r12m=.20),
        "C": tech(100, sma200=90, r1m=None, r3m=None, r6m=None, r12m=None),
    }
    ranked = screener.rank_basket(basket, tbs)
    assert [r["symbol"] for r in ranked] == ["B", "A"]   # C excluded (no score)
    assert ranked[0]["rank_overall"] == 1
    assert ranked[0]["rank_in_category"] == 1
    assert ranked[0]["category_size"] == 2  # both A and B are MCAP


# ---------------- entry filter ----------------

def test_entry_filter_requires_above_200dma():
    ranked = [{"symbol": "A", "category": "MCAP", "score": 0.1,
              "tech": tech(90, sma200=100, rsi14=50)}]
    assert entry_filter.filter_eligible(ranked) == []


def test_entry_filter_blocks_overbought_and_oversold():
    hot = [{"symbol": "A", "category": "MCAP", "score": 0.1,
           "tech": tech(110, sma200=100, rsi14=90)}]
    cold = [{"symbol": "B", "category": "MCAP", "score": 0.1,
            "tech": tech(110, sma200=100, rsi14=10)}]
    fine = [{"symbol": "C", "category": "MCAP", "score": 0.1,
            "tech": tech(110, sma200=100, rsi14=55)}]
    assert entry_filter.filter_eligible(hot) == []
    assert entry_filter.filter_eligible(cold) == []
    assert len(entry_filter.filter_eligible(fine)) == 1


# ---------------- risk engine sizing ----------------

def test_size_sip_splits_across_mcap_with_floor():
    cands = [{"symbol": "A", "score": 0.20}, {"symbol": "B", "score": 0.01}]
    sizes = risk_engine.size_sip(cands, 5000)
    assert abs(sum(sizes.values()) - 5000) < 1e-6
    # both get at least the floor weight, leader gets more
    assert sizes["A"] > sizes["B"]
    assert sizes["B"] >= 5000 * config.SIP_MIN_WEIGHT_PER_MCAP - 1e-6


def test_size_tactical_respects_budget_and_category_cap():
    eligible = [{"symbol": "X", "category": "THEMATIC", "score": 0.3}]
    sizes = risk_engine.size_tactical(eligible, deployable_liquidity=100000,
                                     current_weights={"X": 0.0})
    # budget = 100000 * DAILY_TACTICAL_DEPLOY_FRACTION, all to the one candidate
    expected_budget = 100000 * config.DAILY_TACTICAL_DEPLOY_FRACTION
    assert abs(sizes["X"] - expected_budget) < 1.0


def test_size_tactical_skips_when_already_at_category_cap():
    eligible = [{"symbol": "X", "category": "THEMATIC", "score": 0.3}]
    # already at the thematic max weight -> no room left
    sizes = risk_engine.size_tactical(
        eligible, deployable_liquidity=100000,
        current_weights={"X": config.MAX_WEIGHT_THEMATIC})
    assert "X" not in sizes


# ---------------- exit engine: THE profit gate ----------------

def _state_with_position(symbol, units, cost):
    state = pf._default_state()
    pf.add_lot(state, symbol, units, cost)
    return state


def _ranked_member(symbol, category, close, sma200, rank_in_category, category_size):
    return {"symbol": symbol, "category": category, "score": 0.0,
           "tech": tech(close, sma200=sma200),
           "rank_in_category": rank_in_category, "category_size": category_size}


def test_no_exit_when_no_signal():
    state = _state_with_position("NIFTYBEES", 100, 100)
    # rank 1 of 5, above 200DMA -> no signal at all
    member = _ranked_member("NIFTYBEES", "MCAP", close=120, sma200=100,
                            rank_in_category=1, category_size=5)
    intents = exit_engine.decide_exits(state, [member])
    assert intents == []


def test_signal_but_at_a_loss_holds_no_sell():
    state = _state_with_position("NIFTYBEES", 100, 100)
    # severe signal (below 200DMA) but price is BELOW cost -> at a loss
    member = _ranked_member("NIFTYBEES", "MCAP", close=90, sma200=100,
                            rank_in_category=5, category_size=5)
    intents = exit_engine.decide_exits(state, [member])
    assert intents == []   # THE core guarantee: never sells at a loss


def test_signal_with_thin_profit_below_threshold_holds():
    state = _state_with_position("NIFTYBEES", 100, 100)
    # mild signal, only 1% profit — below BASE_PROFIT_BOOKING_PCT (5% default),
    # and extension can't apply (no rank_overall/total_ranked on this fixture
    # -> not eligible -> bar stays at the flat base anyway)
    member = _ranked_member("NIFTYBEES", "MCAP", close=101, sma200=90,
                            rank_in_category=5, category_size=5)
    intents = exit_engine.decide_exits(state, [member])
    assert intents == []


def test_thematic_full_exit_when_signal_and_sufficient_profit():
    state = _state_with_position("ITBEES", 50, 100)
    member = _ranked_member("ITBEES", "THEMATIC", close=130, sma200=140,
                            rank_in_category=5, category_size=5)  # below 200DMA -> severe
    intents = exit_engine.decide_exits(state, [member])
    assert len(intents) == 1
    assert intents[0]["shares"] == 50   # full exit
    assert intents[0]["category"] == "THEMATIC"


def test_mcap_partial_trim_respects_floor():
    state = _state_with_position("NIFTYBEES", 100, 100)
    state["positions"]["NIFTYBEES"]["peak_units"] = 100  # peak ever held = 100
    # already trimmed once before, near the floor
    state["positions"]["NIFTYBEES"]["lots"] = [{"shares": 51, "price": 100,
                                                "date": dt.date.today().isoformat()}]
    member = _ranked_member("NIFTYBEES", "MCAP", close=130, sma200=140,
                            rank_in_category=5, category_size=5)  # severe -> 20% trim
    intents = exit_engine.decide_exits(state, [member])
    # floor = 50 units (50% of peak 100). Holding 51, severe trim wants 20% of
    # 51 = 10.2, but only 1 unit of room exists above the floor.
    assert len(intents) == 1
    assert abs(intents[0]["shares"] - 1.0) < 1e-6


def test_mcap_trim_cooldown_blocks_repeat():
    state = _state_with_position("NIFTYBEES", 100, 100)
    state["positions"]["NIFTYBEES"]["peak_units"] = 100
    pf.record_trim(state, "NIFTYBEES")  # trimmed today already
    member = _ranked_member("NIFTYBEES", "MCAP", close=130, sma200=140,
                            rank_in_category=5, category_size=5)
    intents = exit_engine.decide_exits(state, [member])
    assert intents == []


def test_mcap_never_goes_to_zero_over_many_signals():
    """Simulate repeated severe signals (with cooldown bypassed) and confirm
    the floor holds — this is the structural guarantee, stress-tested."""
    state = _state_with_position("NIFTYBEES", 1000, 100)
    state["positions"]["NIFTYBEES"]["peak_units"] = 1000
    member = _ranked_member("NIFTYBEES", "MCAP", close=200, sma200=210,
                            rank_in_category=5, category_size=5)
    for _ in range(50):
        state["mcap_trim_last_day"] = {}  # bypass cooldown to stress-test the floor alone
        intents = exit_engine.decide_exits(state, [member])
        for i in intents:
            res = pf.remove_fifo(state, i["symbol"], i["shares"], i["price"])
        if pf.total_units(state, "NIFTYBEES") <= pf.mcap_floor_units(state, "NIFTYBEES") + 1e-6:
            break
    assert pf.total_units(state, "NIFTYBEES") >= 1000 * config.MCAP_MIN_RETAINED_FRACTION - 1e-6


# ---------------- portfolio FIFO / floor mechanics ----------------

def test_avg_cost_and_total_units():
    state = pf._default_state()
    pf.add_lot(state, "NIFTYBEES", 10, 100)
    pf.add_lot(state, "NIFTYBEES", 10, 120)
    assert pf.total_units(state, "NIFTYBEES") == 20
    assert abs(pf.avg_cost(state, "NIFTYBEES") - 110) < 1e-9


def test_fifo_tax_classification():
    state = pf._default_state()
    old = (dt.date.today() - dt.timedelta(days=400)).isoformat()
    new = (dt.date.today() - dt.timedelta(days=10)).isoformat()
    state["positions"]["NIFTYBEES"] = {
        "lots": [{"shares": 10, "price": 100, "date": old},
                {"shares": 10, "price": 120, "date": new}],
        "peak_units": 20,
    }
    res = pf.remove_fifo(state, "NIFTYBEES", 15, 150)
    assert abs(res["ltcg"] - (150 - 100) * 10) < 1e-6
    assert abs(res["stcg"] - (150 - 120) * 5) < 1e-6


# ---------------- profit_extension: the dynamic "let it run" bar ----------------

import profit_extension


def _strong_member(rank=1, total=10):
    """All four extension criteria pass, near-maximal strength."""
    return {"tech": {"close": 110, "sma50": 100, "rsi14": 65, "r1m": 0.08},
           "rank_overall": rank, "total_ranked": total}


def test_extension_not_eligible_if_below_50dma():
    m = _strong_member()
    m["tech"]["close"] = 95  # now below sma50=100
    assert profit_extension.is_eligible(m) is False
    assert profit_extension.required_profit_pct(m) == config.BASE_PROFIT_BOOKING_PCT


def test_extension_not_eligible_if_rsi_out_of_band():
    overbought = _strong_member(); overbought["tech"]["rsi14"] = 90
    oversold = _strong_member(); oversold["tech"]["rsi14"] = 20
    assert profit_extension.is_eligible(overbought) is False
    assert profit_extension.is_eligible(oversold) is False


def test_extension_not_eligible_if_1m_negative():
    m = _strong_member(); m["tech"]["r1m"] = -0.01
    assert profit_extension.is_eligible(m) is False


def test_extension_not_eligible_if_bottom_half_overall():
    m = _strong_member(rank=8, total=10)  # rank 8 of 10 -> bottom half
    assert profit_extension.is_eligible(m) is False


def test_extension_eligible_raises_bar_above_base():
    m = _strong_member(rank=1, total=10)  # strongest possible reading
    req = profit_extension.required_profit_pct(m)
    assert req > config.BASE_PROFIT_BOOKING_PCT
    assert req <= config.BASE_PROFIT_BOOKING_PCT + config.EXTENSION_MAX_BONUS_PCT + 1e-9


def test_extension_scales_with_strength_not_just_pass_fail():
    weak_but_eligible = {"tech": {"close": 100.5, "sma50": 100, "rsi14": 51, "r1m": 0.001},
                         "rank_overall": 5, "total_ranked": 10}
    strong = _strong_member(rank=1, total=10)
    req_weak = profit_extension.required_profit_pct(weak_but_eligible)
    req_strong = profit_extension.required_profit_pct(strong)
    assert profit_extension.is_eligible(weak_but_eligible) is True
    assert config.BASE_PROFIT_BOOKING_PCT < req_weak < req_strong
    assert req_strong <= config.BASE_PROFIT_BOOKING_PCT + config.EXTENSION_MAX_BONUS_PCT + 1e-9


def test_extension_integration_holds_a_winner_past_the_flat_base():
    """The key behavior: a position +6% in profit (above the flat 5% base)
    that ALSO clears all four strength criteria should be HELD, not sold,
    because its dynamic bar is above 6%."""
    state = _state_with_position("ITBEES", 50, 100)
    member = {
        "symbol": "ITBEES", "category": "THEMATIC", "score": 0.0,
        "tech": {"close": 106, "sma50": 100, "sma200": 95, "rsi14": 65, "r1m": 0.08},
        "rank_in_category": 5, "category_size": 5,   # mild signal (rank-bad)
        "rank_overall": 1, "total_ranked": 10,        # but #1 overall -> extension
    }
    req = profit_extension.required_profit_pct(member)
    assert req > 0.06, "test assumes the extended bar exceeds the 6% profit on the table"
    intents = exit_engine.decide_exits(state, [member])
    assert intents == []   # held, despite clearing the flat 5% base


def test_no_extension_same_profit_sells_when_not_eligible():
    """Same +6% profit and signal, but rank is bottom-half overall -> no
    extension -> bar stays at flat 5% -> 6% clears it -> sells."""
    state = _state_with_position("ITBEES", 50, 100)
    member = {
        "symbol": "ITBEES", "category": "THEMATIC", "score": 0.0,
        "tech": {"close": 106, "sma50": 100, "sma200": 95, "rsi14": 65, "r1m": 0.08},
        "rank_in_category": 5, "category_size": 5,
        "rank_overall": 9, "total_ranked": 10,   # bottom half -> NOT eligible
    }
    intents = exit_engine.decide_exits(state, [member])
    assert len(intents) == 1
    assert intents[0]["shares"] == 50


# ---------------- profit_reduction: lock in profit sooner on weakness ----------------

import profit_reduction
import profit_bar


def _weak_member(rank=10, total=10):
    """All four reduction criteria pass, near-maximal weakness."""
    return {"tech": {"close": 95, "sma50": 105, "rsi14": 25, "r1m": -0.08},
           "rank_overall": rank, "total_ranked": total}


def test_reduction_not_eligible_if_above_50dma():
    m = _weak_member(); m["tech"]["close"] = 110  # now above sma50=105
    assert profit_reduction.is_eligible(m) is False
    assert profit_reduction.required_profit_pct(m) == config.BASE_PROFIT_BOOKING_PCT


def test_reduction_not_eligible_if_rsi_not_weak_enough():
    m = _weak_member(); m["tech"]["rsi14"] = 50  # at the ceiling, not below it
    assert profit_reduction.is_eligible(m) is False


def test_reduction_not_eligible_if_1m_positive():
    m = _weak_member(); m["tech"]["r1m"] = 0.01
    assert profit_reduction.is_eligible(m) is False


def test_reduction_not_eligible_if_top_half_overall():
    m = _weak_member(rank=2, total=10)
    assert profit_reduction.is_eligible(m) is False


def test_reduction_eligible_lowers_bar_below_base():
    m = _weak_member(rank=10, total=10)  # strongest possible weakness reading
    req = profit_reduction.required_profit_pct(m)
    assert req < config.BASE_PROFIT_BOOKING_PCT
    assert req >= config.BASE_PROFIT_BOOKING_PCT - config.REDUCTION_MAX_PCT - 1e-9


def test_reduction_hits_the_275_floor_at_max_weakness():
    m = {"tech": {"close": 90, "sma50": 110, "rsi14": 0, "r1m": -0.20},
        "rank_overall": 10, "total_ranked": 10}
    req = profit_reduction.required_profit_pct(m)
    assert abs(req - 0.0275) < 1e-6   # 5% base - 2.25% max reduction = 2.75%


def test_reduction_scales_with_weakness_not_just_pass_fail():
    barely = {"tech": {"close": 99.5, "sma50": 100, "rsi14": 44, "r1m": -0.001},
             "rank_overall": 6, "total_ranked": 10}
    severe = _weak_member(rank=10, total=10)
    req_barely = profit_reduction.required_profit_pct(barely)
    req_severe = profit_reduction.required_profit_pct(severe)
    assert profit_reduction.is_eligible(barely) is True
    assert req_severe < req_barely < config.BASE_PROFIT_BOOKING_PCT


def test_profit_bar_combinator_picks_extension():
    m = _strong_member(rank=1, total=10)
    assert profit_bar.required_profit_pct(m) == profit_extension.required_profit_pct(m)


def test_profit_bar_combinator_picks_reduction():
    m = _weak_member(rank=10, total=10)
    assert profit_bar.required_profit_pct(m) == profit_reduction.required_profit_pct(m)


def test_profit_bar_combinator_neutral_case_is_exactly_base():
    neutral = {"tech": {"close": 100, "sma50": 100, "rsi14": 48, "r1m": 0.0},
              "rank_overall": 5, "total_ranked": 10}
    assert profit_bar.required_profit_pct(neutral) == config.BASE_PROFIT_BOOKING_PCT


def test_reduction_integration_sells_a_fading_winner_below_the_flat_base():
    """The key behavior: a position +3.5% in profit (BELOW the flat 5% base)
    that ALSO clears all four weakness criteria should be SOLD, not held,
    because its reduced bar (~2.9%, verified numerically) is below 3.5%."""
    state = _state_with_position("ITBEES", 50, 100)
    member = {
        "symbol": "ITBEES", "category": "THEMATIC", "score": 0.0,
        "tech": {"close": 103.5, "sma50": 115, "sma200": 95, "rsi14": 12, "r1m": -0.12},
        "rank_in_category": 5, "category_size": 5,    # mild signal (rank-bad)
        "rank_overall": 10, "total_ranked": 10,        # bottom-half -> reduction eligible
    }
    req = profit_reduction.required_profit_pct(member)
    assert req < 0.035, "test assumes the reduced bar is below the 3.5% profit on the table"
    intents = exit_engine.decide_exits(state, [member])
    assert len(intents) == 1
    assert intents[0]["shares"] == 50
