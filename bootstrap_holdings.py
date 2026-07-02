"""
bootstrap_holdings.py — seed state/portfolio.json from your ACTUAL Kite
holdings, instead of starting the engine from zero and waiting on a ₹5k/month
SIP to slowly rebuild exposure you already have.

================================================================================
THE SAFETY GUARANTEE THIS SCRIPT EXISTS TO ENFORCE:
candidates.csv is an explicit, manually-curated ETF WHITELIST. This script —
and the entire trading engine — will NEVER touch any symbol that isn't
literally listed there, full stop. No heuristics, no "looks like an ETF"
inference, no partial matching, no defaulting-to-include. kite.holdings()
returns EVERY holding in the account, stocks included; only symbols present
in candidates.csv are ever turned into a managed lot. Everything else —
every stock, every ETF you haven't explicitly added — is left completely
alone, permanently, by construction.

The only thing this script does beyond that hard gate is a SOFT, ADVISORY
check: it looks at the broker's own descriptive name for each ignored holding
and flags any that contain the word "ETF" as a possible miss — i.e. "this
looks like it might be an ETF you forgot to whitelist, you may want to check."
That flag changes nothing automatically. It's purely a suggestion printed to
the screen. A stock is never reclassified, never touched, never at risk.
================================================================================

Why this, not the screenshot: a portfolio screenshot shows current VALUE, not
quantity or cost basis — and cost basis is exactly what the exit engine's
profit gate needs. kite.holdings() gives the real quantity and average_price
straight from the broker, which is the only honest source for that.

What this does NOT know: your actual purchase DATE (Kite's holdings() API
doesn't return it). Tax classification (STCG vs LTCG) needs one. Default
assumption: every bootstrapped lot is dated ASSUMED_HOLDING_AGE_DAYS ago
(default 400 — i.e. "already long-term"), the realistic assumption for an
existing multi-year portfolio. If you know the real purchase date for any
holding, put it in bootstrap_dates.csv (symbol,purchase_date) and this script
will use that instead.

Usage:
    python bootstrap_holdings.py              # dry run — shows what it WOULD do
    python bootstrap_holdings.py --apply       # actually writes state/portfolio.json
    python bootstrap_holdings.py --show-ignored  # also lists EVERY ignored
                                                  # holding (including stocks)
                                                  # for a full manual audit
"""

import argparse
import csv
import datetime as dt
import logging
import os

import config
import portfolio as pf
from kite_auth import get_kite

log = logging.getLogger("bootstrap")

ASSUMED_HOLDING_AGE_DAYS = 400   # > 365, so default tax treatment is LTCG
BOOTSTRAP_DATES_FILE = "bootstrap_dates.csv"   # optional: symbol,purchase_date


def _load_etf_whitelist() -> dict:
    """candidates.csv IS the whitelist. Returns {symbol: category}. Anything
    not a key in this dict is permanently out of scope for this script."""
    with open(config.CANDIDATES_FILE) as f:
        return {row["symbol"]: row["category"] for row in csv.DictReader(f)}


def _load_known_dates() -> dict:
    if not os.path.exists(BOOTSTRAP_DATES_FILE):
        return {}
    with open(BOOTSTRAP_DATES_FILE) as f:
        return {row["symbol"]: row["purchase_date"] for row in csv.DictReader(f)}


def _instrument_names(kite) -> dict:
    """tradingsymbol -> broker's descriptive name, for the soft 'looks like an
    ETF' advisory check on ignored holdings. Never used to gate anything."""
    try:
        return {i["tradingsymbol"]: i.get("name", "") for i in kite.instruments(config.EXCHANGE)}
    except Exception as e:
        log.warning("Could not fetch instrument names for the advisory ETF "
                   "check (%s) — skipping that check, the whitelist gate "
                   "itself is unaffected.", e)
        return {}


def build_state(holdings: list[dict], whitelist: dict, known_dates: dict) -> tuple[dict, list[dict]]:
    """
    Returns (state, ignored) where `ignored` is EVERY holding not in the
    whitelist — stocks and any un-whitelisted ETFs alike. Nothing in
    `ignored` is ever touched; it's returned purely for reporting.
    """
    state = pf._default_state()
    state["cash"] = 0.0
    ignored = []
    default_date = (dt.date.today() - dt.timedelta(days=ASSUMED_HOLDING_AGE_DAYS)).isoformat()

    for h in holdings:
        sym = h["tradingsymbol"]
        qty = float(h.get("quantity", 0)) + float(h.get("t1_quantity", 0))
        avg_price = float(h.get("average_price", 0))
        if qty <= 0:
            continue

        if sym == config.LIQUID_ETF:
            state["liquidbees_units"] = qty
            log.info("War chest seeded from existing %s holding: %.4f units "
                     "(~Rs %.0f at avg cost).", sym, qty, qty * avg_price)
            continue

        if sym not in whitelist:
            ignored.append({"symbol": sym, "qty": qty, "avg_price": avg_price,
                           "value": qty * avg_price})
            continue

        lot_date = known_dates.get(sym, default_date)
        pos = state["positions"].setdefault(sym, {"lots": [], "peak_units": 0.0})
        pos["lots"].append({"shares": qty, "price": avg_price, "date": lot_date})
        pos["peak_units"] = qty   # today's holding becomes the floor anchor
        log.info("Mapped %-14s (%s): %.4f units @ avg Rs %.2f, dated %s%s",
                 sym, whitelist[sym], qty, avg_price, lot_date,
                 " [assumed]" if sym not in known_dates else " [from bootstrap_dates.csv]")

    return state, ignored


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                       help="Actually write state/portfolio.json. Without this "
                            "flag, only previews what would happen.")
    parser.add_argument("--show-ignored", action="store_true",
                       help="List EVERY holding not in the ETF whitelist, "
                            "including every stock. Default is quiet about "
                            "these except a count and a possible-ETF check.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if os.path.exists(config.PORTFOLIO_FILE):
        log.warning("state/portfolio.json already exists. This script will "
                   "OVERWRITE it if run with --apply. Back it up first if "
                   "you've already been running the engine.")

    kite = get_kite()
    holdings = kite.holdings()
    whitelist = _load_etf_whitelist()
    known_dates = _load_known_dates()

    print(f"\nETF whitelist loaded: {len(whitelist)} symbols from "
         f"{config.CANDIDATES_FILE}. ONLY these will ever be touched — "
         f"every stock holding and every un-whitelisted ETF is permanently "
         f"out of scope.\n")

    state, ignored = build_state(holdings, whitelist, known_dates)

    print("=" * 60)
    print("BOOTSTRAP PREVIEW" if not args.apply else "BOOTSTRAP — APPLYING")
    print("=" * 60)

    total_mapped_value = 0.0
    for sym, pos in state["positions"].items():
        units = sum(l["shares"] for l in pos["lots"])
        avg = sum(l["shares"] * l["price"] for l in pos["lots"]) / units if units else 0
        value = units * avg
        total_mapped_value += value
        print(f"  {sym:<14} {whitelist.get(sym,'?'):<10} "
             f"{units:>10.4f} units  @ avg Rs {avg:>9.2f}  = Rs {value:>10,.0f}")

    print(f"\n  War chest ({config.LIQUID_ETF}): {state['liquidbees_units']:.4f} units")
    print(f"  Total mapped value: Rs {total_mapped_value:,.0f}")

    # soft advisory: anything ignored whose broker-listed name contains "ETF"
    names = _instrument_names(kite) if ignored else {}
    possible_misses = [u for u in ignored
                       if "etf" in names.get(u["symbol"], "").lower()]

    print(f"\n  Ignored {len(ignored)} holding(s) not in the whitelist "
         f"(your stocks + any un-whitelisted ETFs) — these are NEVER touched.")

    if possible_misses:
        print(f"\n  ⚠ {len(possible_misses)} of those LOOK like ETFs based on "
             f"their listing name — review and add to {config.CANDIDATES_FILE} "
             f"if intentional (symbol,MCAP-or-THEMATIC,notes):")
        for u in possible_misses:
            print(f"      {u['symbol']:<20} \"{names.get(u['symbol'],'')}\"  "
                 f"qty={u['qty']:.4f}  avg=Rs{u['avg_price']:.2f}  value=Rs{u['value']:,.0f}")

    if args.show_ignored:
        print(f"\n  Full ignored list ({len(ignored)}):")
        for u in ignored:
            tag = " [possible ETF]" if u in possible_misses else ""
            print(f"      {u['symbol']:<20} qty={u['qty']:.4f}  "
                 f"avg=Rs{u['avg_price']:.2f}  value=Rs{u['value']:,.0f}{tag}")
    elif ignored:
        print(f"  (run with --show-ignored to see the full list, including your stocks)")

    print()
    if not known_dates:
        print(f"  Note: no {BOOTSTRAP_DATES_FILE} found — every lot above is dated "
             f"{ASSUMED_HOLDING_AGE_DAYS} days ago (assumed long-term). If you know "
             f"the real purchase dates, create {BOOTSTRAP_DATES_FILE} with rows of "
             f"'symbol,purchase_date' (YYYY-MM-DD) and re-run.\n")

    if args.apply:
        pf.save(state)
        print(f"  Written to {config.PORTFOLIO_FILE}.")
        print("  Run main.py next — the engine will manage these positions "
             "going forward (exits are still profit-gated as normal; nothing "
             "gets sold just because it was bootstrapped).\n")
    else:
        print("  This was a DRY RUN — nothing written. Re-run with --apply "
             "once the mapping above looks right.\n")


if __name__ == "__main__":
    main()
