# ETF Shop — Momentum Rotation Engine

A delivery-only, tax-aware, multi-ETF momentum rotation system for Kite
Connect, built to run unattended on a free-tier VM. No F&O. No intraday.
Every order is `CNC` `LIMIT`. **No position is ever sold at a loss — full
stop.** That single rule shapes everything else below.

## The strategy

**1. Universe — a liquidity-screened basket of 10.**
`candidates.csv` lists ~20 starter NSE ETFs tagged `MCAP` (broad market-cap
trackers: Nifty 50, Next 50, Midcap 150...) or `THEMATIC` (sectoral/thematic:
IT, Bank, Pharma, PSU Bank...). Once a month, `universe.py` validates each
symbol against the live broker instrument list, scores it on 20-day average
daily traded value, drops anything under `MIN_ADTV_INR`, and keeps the top 10
— with at least `MIN_MCAP_IN_BASKET` reserved for MCAP so the core sleeve is
never empty. **Treat the CSV as a starter list, not gospel** — tickers get
renamed, delisted, or merged; the screen is designed to gracefully skip
anything that doesn't resolve rather than crash.

**2. Ranking — composite momentum.**
Each basket member gets a momentum score: a weighted blend of 1m/3m/6m/12m
trailing returns (`config.MOMENTUM_WEIGHTS`). Ranked overall and within its
own category (MCAP vs THEMATIC), since the SIP only ever looks at the MCAP
ranking.

**3. Entry filter — ranking alone isn't enough to buy.**
A candidate must also be above its 200DMA (long-term uptrend) and have RSI(14)
between 30 and 75 (not a falling knife, not a blow-off top) before it's
eligible for *any* purchase today.

**4. Buying — two separate engines, two separate budgets.**
- **SIP (forced, monthly, MCAP-only):** the fixed ₹5,000/month always fires,
  split across eligible MCAP members weighted by momentum (with a floor so
  one name never eats 100%). This is the forced-participation guarantee — it
  never skips a month, never waits for a "better" entry.
- **Tactical (daily, only if criteria met):** sized off **accumulated
  liquidity** — cash plus the LIQUIDBEES war chest — not a fixed rupee amount.
  A flush war chest buys bigger; a thin one barely trades. Splits across the
  top-`TACTICAL_TOP_K` eligible candidates (any category) weighted by score,
  capped per-symbol by category (`MAX_WEIGHT_MCAP` / `MAX_WEIGHT_THEMATIC`).

**5. Selling — profit-gated, category-asymmetric, never at a loss.**
A sell needs TWO things to both be true:
   - a **signal**: momentum rank fell into the bottom half of its category,
     and/or price broke below its 200DMA (the latter = "severe", else "mild");
   - **profit**: unrealized gain at or above a **dynamic bar that moves in
     both directions** (`profit_bar.py`, combining `profit_extension.py` and
     `profit_reduction.py`).

  The bar starts at `BASE_PROFIT_BOOKING_PCT` (5%):
   - **Extension** — if the position shows genuine ongoing *strength* (above
     its 50DMA, RSI 50-80, 1-month return positive, AND top-half of the
     *whole* basket — not just "least bad" in its own weak category), the bar
     rises toward 10%, scaled by how strongly it clears those checks. A
     winner that's still clearly working gets more room to run.
   - **Reduction** — the mirror image: if the position shows genuine ongoing
     *weakness* (below its 50DMA, RSI under 45, 1-month return negative, AND
     bottom-half of the whole basket), the bar falls toward 2.75%, scaled by
     how strongly it shows that weakness. Since this system can never sell at
     a loss, a fading position has a closing window — locking in a smaller
     profit sooner beats waiting and risking the gain erode away entirely.
   - Miss even one of the four criteria on either side → flat 5%, no adjustment.

  If the signal fires but the position is below its required bar — 5% flat,
  9% extended, or 3% reduced, whatever it computes to — **nothing happens. It
  is simply held.** No stop-loss exists anywhere in this system, for either
  category. Once the bar clears:
   - **THEMATIC** → full exit. A broken theme gets cut clean, profit booked.
   - **MCAP** → trimmed 10% (mild) or 20% (severe), but structurally floored
     at `MCAP_MIN_RETAINED_FRACTION` (50%) of the most units it ever held, and
     rate-limited to one trim per `MCAP_TRIM_COOLDOWN_DAYS` (~20 trading days)
     per symbol. A MCAP core position can never be trimmed to zero —
     this is stress-tested in `tests/test_engine.py`.

  Sell proceeds land in cash, then get swept into LIQUIDCASE before any same-
  day buy is sized — booked profit gets *parked*, not instantly redeployed.

## The honest tradeoff this creates

MCAP positions are safe to floor-protect because a broad index basket can't
go to zero and tends to mean-revert across cycles. **A single sector doesn't
have that guarantee.** Combined with "never sell at a loss," a thematic ETF
that's both losing *and* fundamentally broken (a structural multi-year sector
down-cycle, not just a rough patch) will simply sit there forever — it's
fully exitable, but only on the way up. That's a real, asymmetric risk
specific to the THEMATIC sleeve that the MCAP sleeve doesn't share. Worth
weighing deliberately, not something to discover later.

## Starting from an existing portfolio (not from zero)

If you already hold ETFs in Kite — alongside stock holdings that must never
be touched — bootstrap from your real holdings instead of waiting on a
₹5k/month SIP to slowly rebuild exposure you already have:

```bash
python bootstrap_holdings.py                # preview — shows the mapping, writes nothing
python bootstrap_holdings.py --apply         # writes state/portfolio.json for real
python bootstrap_holdings.py --show-ignored  # full audit of every ignored holding
```

**The safety guarantee:** `candidates.csv` is an explicit, manually-curated
ETF whitelist. This script — and the entire trading engine — will never touch
any symbol that isn't literally listed there. No heuristics, no "looks like
an ETF" inference, no defaulting to include. `kite.holdings()` returns
*everything* in your account, stocks included; only whitelisted symbols ever
become a managed lot. Your stock holdings are reported in a single count and
otherwise left completely alone — by construction, not by convention. Smoke-
tested directly: a mock holdings list with RELIANCE and TCS mixed in confirms
neither ever enters the engine's position state.

The one soft check on top of that hard gate: any *ignored* holding whose
broker-listed name contains "ETF" gets flagged as a possible miss — e.g. an
ETF you forgot to whitelist. That's purely advisory text printed to the
screen; it changes nothing automatically, and it's specifically built to
never flag an actual stock (verified in the same smoke test).

This calls `kite.holdings()` directly — real quantity and real average cost
from the broker, not guessed off a screenshot (a screenshot only shows
current *value*, which is useless for the profit gate; cost basis is what
actually matters). Each whitelisted holding becomes a starting FIFO lot, and
**today's quantity becomes the MCAP floor anchor** — the engine will never
trim a bootstrapped MCAP holding below 50% of what you already own. Your
existing LIQUIDCASE holding becomes the starting war chest directly.

One honest gap: **purchase date isn't in the Kite holdings API.** Every
bootstrapped lot defaults to "400 days ago" (already long-term/LTCG-eligible)
— the realistic assumption for an existing multi-year portfolio. If you know
the real dates, create `bootstrap_dates.csv` (`symbol,purchase_date`) and
it'll use those instead.

`candidates.csv` ships pre-populated with ~26 commonly-known NSE equity ETFs
across both categories — edit it freely, that's the point. **Intentionally
excluded:** Gold/Silver ETFs and debt/G-Sec ETFs. They have entirely
different tax treatment (gold/silver: always STCG-rate regardless of holding
period; debt: slab-rate) and don't fit this equity-momentum framework's
exit logic. If you want to manage those, treat them as a separate, simpler
system rather than bolting them onto this one.

## Files

| File | Role |
|---|---|
| `config.py` | every threshold, with the *why* documented inline |
| `candidates.csv` | starter ETF universe (symbol, category) — verify, don't trust |
| `bootstrap_holdings.py` | seed state from REAL Kite holdings, not a screenshot |
| `universe.py` | monthly liquidity screen → basket of 10 |
| `indicators.py` | SMA/RSI/momentum-score, pure functions |
| `screener.py` | ranks the basket, overall + per category |
| `entry_filter.py` | trend/RSI gate — who's even eligible to buy today |
| `risk_engine.py` | position sizing off accumulated liquidity, category-capped |
| `profit_extension.py` | raises the profit bar for genuine ongoing strength |
| `profit_reduction.py` | lowers the profit bar for genuine ongoing weakness |
| `profit_bar.py` | combines both into the single threshold exit_engine uses |
| `exit_engine.py` | the profit-gated, category-asymmetric sell logic |
| `liquidity_buffer.py` | LIQUIDCASE sweep, deployable-liquidity calc, draw-down |
| `portfolio.py` | multi-symbol FIFO lots, peak-units (for the MCAP floor), state |
| `strategy.py` | orchestrates universe → screener → entries/exits → sizing |
| `order_engine.py` | the *only* module placing orders (CNC LIMIT); sells→sweep→buys |
| `kite_auth.py` | daily login — interactive or unattended TOTP |
| `data_fetcher.py` | rate-limited Kite history/quote wrapper |
| `monitoring.py` | logging + appended multi-symbol telemetry |
| `main.py` | daily orchestration entrypoint (run via systemd/cron) |
| `backtest.py` | rough historical sanity check — reuses the REAL decision modules |
| `tests/` | unit tests, including a stress test of the MCAP floor |
| `deploy/` | systemd service + timer |
| `DEPLOY.md` | EC2 / Oracle free-tier setup guide |

## Quick start

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && chmod 600 .env   # fill in keys
python kite_auth.py                      # one-time interactive login
pytest tests/ -v
python bootstrap_holdings.py --apply     # if you already hold ETFs — see above
python backtest.py                       # rough sanity check on real history
python main.py                           # DRY_RUN=True by default
```

Set `DRY_RUN = False` in `config.py` only after paper-trading through at least
one real correction (you want to *watch* the profit-gate hold a losing
position and the trend-break trigger a trim, not just trust the code). See
`DEPLOY.md` for Oracle/AWS free-tier server setup.

## Tuning

The knobs that change behavior the most:
- `candidates.csv` — your actual universe; verify every symbol against current
  NSE listings and your broker before trusting it.
- `MIN_PROFIT_TO_EXIT_PCT` — raise it and the system trades less, holds
  through more noise; lower it (toward 0) and it books smaller, more frequent
  wins — but frequent small bookings on MCAP eat into the cooldown budget fast.
- `MCAP_MIN_RETAINED_FRACTION` — the structural floor. Raise toward 1.0 for a
  near-permanent core; lower it if you want the tactical trims to matter more.
- `TACTICAL_TOP_K` / `DAILY_TACTICAL_DEPLOY_FRACTION` — how aggressively the
  war chest gets deployed on a given day's signal.

## Honest limitations

- `backtest.py` reuses the real decision logic but simplifies execution: flat
  cost haircut, no LIQUIDBEES NAV drift modeled, no limit-order non-fills.
  It's a sanity check on the *logic*, not a return forecast.
- `state/portfolio.json` is local bookkeeping, not the broker's truth —
  reconcile against `kite.holdings()` monthly.
- Unattended TOTP login means your 2FA secret lives on the VM — a real risk
  decision; see `DEPLOY.md` for alternatives.
- The basket's liquidity screen is only as good as `candidates.csv`. Review it
  at least as often as the basket rebalances.

## A note on government-service constraints

Cash-delivery only — no F&O, no intraday — so this sits outside the clearest
speculative-trading restrictions. But *automated/systematic* trading via API
is a separate question from whether the trades themselves are permitted.
Worth confirming clearance before flipping `DRY_RUN` off, same flag carried
over from the earlier single-ETF version of this repo.
