"""
config.py — Momentum Rotation Engine across a liquidity-screened ETF basket.

Strategy in one sentence:
    Each month, screen ~20 candidate NSE ETFs down to the 10 most liquid;
    rank them by momentum; fixed monthly SIP always buys into the strongest
    market-cap-based (MCAP) ETFs; daily, IF criteria are met, tactically buy
    the strongest sectoral/thematic candidates sized off total accumulated
    liquidity (cash + LIQUIDCASE); exits are asymmetric — thematic/sectoral
    can be fully cut, MCAP core holdings are only ever trimmed 10-20% and
    structurally floored so they can never go to zero.

Why two categories with different sell rules (the core design decision):
    MCAP ETFs (NIFTYBEES, JUNIORBEES, midcap/broad trackers) hold baskets of
    100+ stocks across the whole economy — they mean-revert across cycles and
    structurally cannot go to zero. A bad momentum read on "the market" is
    usually noise, not a reason to abandon the position entirely. So they're
    the forced-hold core: SIP always lands here, exits are shallow trims with
    a hard floor.
    Thematic/sectoral ETFs (IT, Pharma, PSU Bank, etc.) hold 10-30 stocks in
    one slice of the economy. A theme can be structurally broken for years
    (a regulatory shift, a multi-year sector down-cycle) with no mean-reversion
    guarantee. These are tactical satellite bets: sized smaller, and when the
    momentum/trend signal turns, fully exited rather than nursed.

All money in INR. All orders CNC (delivery) LIMIT. No F&O, no intraday.
"""

# ---------------------------------------------------------------------------
# Universe — candidate list lives in candidates.csv (symbol, category).
# This is a STARTER list, same spirit as the old sector_map.csv: verify each
# symbol resolves via kite.instruments() before trusting it (universe.py does
# this automatically and skips/logs anything that doesn't resolve — tickers
# get renamed/delisted/merged over time, don't trust this blindly).
# ---------------------------------------------------------------------------
CANDIDATES_FILE = "candidates.csv"
CATEGORY_MCAP = "MCAP"          # broad market-cap-based — core, forced-hold
CATEGORY_THEMATIC = "THEMATIC"  # sectoral/thematic — tactical, fully exitable

BASKET_SIZE = 10                 # final basket size after liquidity screen
MIN_MCAP_IN_BASKET = 4           # always keep at least this many MCAP slots
                                 # (guarantees the SIP always has a core to buy)
MIN_ADTV_INR = 5_00_00_000       # ₹5 Cr minimum 20-day avg daily traded value
                                 # to even be considered "sufficiently liquid"
LIQUIDITY_LOOKBACK_DAYS = 20

UNIVERSE_REBALANCE = "monthly"   # re-screen the basket once a month; momentum
                                 # ranking + buy/sell signals are checked daily
LIQUID_ETF = "LIQUIDCASE"        # Zerodha Nifty 1D Rate Liquid ETF — war-chest
                                 # parking. NOTE: this was LIQUIDBEES (Nippon's
                                 # liquid ETF) in an earlier draft of this
                                 # config; corrected to LIQUIDCASE since that's
                                 # what's actually held in the account this is
                                 # being deployed against. If you ever hold a
                                 # different liquid ETF, change this one line —
                                 # nothing else in the codebase hardcodes the name.

EXCHANGE = "NSE"

# ---------------------------------------------------------------------------
# Cash flow — SIP fixed monthly, tactical buys daily-if-eligible
# ---------------------------------------------------------------------------
MONTHLY_CONTRIBUTION = 5000
SIP_DAY_OF_MONTH = 1
SIP_FRACTION_TO_MCAP = 1.0       # the whole SIP buys MCAP core ETFs, always.
                                 # Thematic bets are NEVER funded by the forced
                                 # SIP — only by tactical daily deployment below.
SIP_MIN_WEIGHT_PER_MCAP = 0.10   # floor so the SIP doesn't 100%-concentrate
                                 # into a single "winning" MCAP ETF some months

# ---------------------------------------------------------------------------
# Momentum scoring — composite of 1m/3m/6m/12m returns.
# Weighted toward medium/long term (the literature's sweet spot) with a small
# 1m component for responsiveness, since daily tactical buys need *some*
# short-horizon signal or they'd never differ day to day.
# ---------------------------------------------------------------------------
MOMENTUM_WEIGHTS = {"r1m": 0.15, "r3m": 0.25, "r6m": 0.30, "r12m": 0.30}

# ---------------------------------------------------------------------------
# Entry filter — gates BEFORE a candidate is even eligible to be bought today
# ---------------------------------------------------------------------------
ENTRY_REQUIRE_ABOVE_200DMA = True   # long-term uptrend confirmation
ENTRY_MAX_RSI = 75                  # don't chase a blow-off top
ENTRY_MIN_RSI = 30                  # momentum strategy: don't buy a falling knife
TACTICAL_TOP_K = 3                  # daily tactical buys only consider the top-K
                                    # ranked eligible candidates (any category)

# ---------------------------------------------------------------------------
# Position sizing — sized off ACCUMULATED LIQUIDITY (cash + LIQUIDCASE value),
# not off a fixed rupee tranche. A flush war chest buys bigger; a thin one
# barely trades. Score-weighted allocation among today's eligible candidates.
# ---------------------------------------------------------------------------
DAILY_TACTICAL_DEPLOY_FRACTION = 0.06   # max 6% of deployable liquidity spent
                                        # on tactical buys in a single day
MAX_WEIGHT_MCAP = 0.30                  # one MCAP ETF can be at most 30% of book
MAX_WEIGHT_THEMATIC = 0.15              # one thematic bet capped tighter — it
                                        # can be fully cut, but shouldn't be
                                        # oversized while it's still alive
MIN_ORDER_VALUE = 1000

# ---------------------------------------------------------------------------
# Exit rules — THE asymmetry, now ALSO gated by profit. Two independent gates
# must both pass before any sell fires:
#   (1) a momentum/trend signal says "reduce or exit" (category-dependent size)
#   (2) the position is actually sitting at a profit >= MIN_PROFIT_TO_EXIT_PCT
# If (1) fires but (2) doesn't, the position is simply held — no exception.
# This is a deliberate continuation of the no-stop-loss design: nothing in this
# system EVER sells at a loss. Booked profit is the only thing that funds an
# exit, and it gets parked back into LIQUIDCASE rather than instantly
# redeployed, so it's sitting in the war chest earning yield until the next
# buy signal actually qualifies for it.
#
# The honest tradeoff this creates: a thematic ETF that's both losing AND
# fundamentally broken will just sit there forever, since it can be fully
# exited but never AT a loss. That's a real, structurally different risk than
# the MCAP floor (which is safe because a broad index can't go to zero) — a
# single sector genuinely can stay impaired for years. Worth weighing.
# ---------------------------------------------------------------------------
EXIT_RANK_CUTOFF_FRACTION = 0.5   # falls into bottom half of its category's
                                  # ranking within the basket -> mild sell signal
EXIT_REQUIRE_BELOW_200DMA = True  # price < 200DMA -> severe sell signal (size only;
                                  # does NOT override the profit gate)

# The base profit bar (BASE_PROFIT_BOOKING_PCT) can move in BOTH directions:
# - profit_extension.py raises it for genuine ongoing STRENGTH (let winners run)
# - profit_reduction.py lowers it for genuine ongoing WEAKNESS (lock in
#   whatever profit is on the table sooner, before it erodes further — since
#   this system can never sell at a loss, waiting too long on a weakening
#   position risks the profit window closing and being stuck holding forever)
# profit_bar.py combines both into the single number exit_engine actually uses.
#
# Extension (read profit_extension.py for the full mechanism): if a position
# is above its 50DMA, RSI healthy (50-80), 1-month return positive, AND
# top-half of the WHOLE basket by momentum — ALL four — the bar rises from
# 5% toward 10%, scaled by how strongly it clears those checks.
BASE_PROFIT_BOOKING_PCT = 0.05    # the anchor — 5%, matched to your current setting
EXTENSION_REQUIRE_ABOVE_50DMA = True
EXTENSION_RSI_FLOOR = 50          # must show real strength, not barely positive
EXTENSION_RSI_CEILING = 80        # ...but not already blown off
EXTENSION_REQUIRE_POSITIVE_1M = True
EXTENSION_REQUIRE_TOP_HALF_OVERALL = True   # top half of the FULL basket by
                                            # momentum, not just its category —
                                            # the genuine-strength check
EXTENSION_MIN_BONUS_PCT = 0.005   # +0.5% — weakest qualifying extension
EXTENSION_MAX_BONUS_PCT = 0.05    # +5.0% — strongest possible extension
                                  # (effective cap: 5% base + 5% bonus = 10%)

# Reduction (read profit_reduction.py for the full mechanism) — the mirror
# image: below its 50DMA, RSI weak (<45), 1-month return negative, AND
# bottom-half of the WHOLE basket — ALL four — the bar falls from 5% toward
# 2.75%, scaled by how strongly it shows that weakness.
REDUCTION_REQUIRE_BELOW_50DMA = True
REDUCTION_RSI_CEILING = 45        # must show real weakness, not just soft
REDUCTION_REQUIRE_NEGATIVE_1M = True
REDUCTION_REQUIRE_BOTTOM_HALF_OVERALL = True
REDUCTION_MIN_PCT = 0.0025        # -0.25% — weakest qualifying reduction (bar: 4.75%)
REDUCTION_MAX_PCT = 0.0225        # -2.25% — strongest possible reduction
                                  # (effective floor: 5% base - 2.25% = 2.75%)

SELL_FRACTION_MCAP_MILD = 0.10    # rank-bad but trend still intact -> trim 10%
SELL_FRACTION_MCAP_SEVERE = 0.20  # trend broken (<200DMA) -> trim 20%
MCAP_MIN_RETAINED_FRACTION = 0.50 # HARD FLOOR: can never trim a MCAP holding
                                  # below 50% of the peak units it ever held.
                                  # This is what makes "never fully exit" real
                                  # even under a long string of weak signals.
MCAP_TRIM_COOLDOWN_DAYS = 20      # don't trim the same MCAP ETF more than
                                  # ~once a month — prevents repeated trims
                                  # from compounding toward the floor too fast

SELL_FRACTION_THEMATIC = 1.0      # full exit when it fires — but ONLY if in
                                  # profit. A losing thematic bet is simply
                                  # held, same as everything else in this system.

# proceeds from ANY sell land in cash, then get swept into LIQUIDCASE on the
# same run by liquidity_buffer.py — never instantly spent on a same-day buy.
ROUTE_SELL_PROCEEDS_TO_WARCHEST = True

# ---------------------------------------------------------------------------
# Order execution
# ---------------------------------------------------------------------------
ORDER_TYPE = "LIMIT"
LIMIT_BUFFER_PCT = 0.003
MAX_SLIPPAGE_PCT = 0.01
MAX_ORDERS_PER_DAY = 12           # higher than the single-ETF version since
                                  # we may touch several symbols + LIQUIDCASE

# ---------------------------------------------------------------------------
# Liquidity buffer (LIQUIDCASE sweep)
# ---------------------------------------------------------------------------
CASH_BUFFER_FLOOR = 2000          # always keep this much as raw cash, sweep
                                  # everything above it into LIQUIDCASE daily

# ---------------------------------------------------------------------------
# Tax model (FY 2026-27)
# ---------------------------------------------------------------------------
STCG_RATE = 0.20
LTCG_RATE = 0.125
LTCG_ANNUAL_EXEMPT = 125000

# ---------------------------------------------------------------------------
# Rate limiting (Kite Connect)
# ---------------------------------------------------------------------------
HIST_RATE_LIMIT_S = 0.34
QUOTE_RATE_LIMIT_S = 0.11
ORDER_RATE_LIMIT_S = 1.05

# ---------------------------------------------------------------------------
# Files / state
# ---------------------------------------------------------------------------
STATE_DIR = "state"
PORTFOLIO_FILE = "state/portfolio.json"
BASKET_FILE = "state/basket.json"
RUN_LOG_DIR = "logs"
TELEMETRY_FILE = "telemetry/equity_history.csv"
TRADES_FILE = "telemetry/trades_history.csv"

# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------
DRY_RUN = True                    # flip to False only after paper-validation
