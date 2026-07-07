"""
app.py — Flask backend for ETF Shop web app.

Wraps the strategy logic and exposes endpoints:
  POST /api/run-strategy       — execute a full run
  GET  /api/portfolio          — current holdings + value
  GET  /api/telemetry          — recent equity history
  GET  /api/auth-status        — check if logged in
  POST /api/login              — Kite login
  POST /api/logout             — clear token
"""

from __future__ import annotations

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from functools import wraps

from flask import Flask, jsonify, request, session
from flask_cors import CORS

# Add ETF_Shop modules to path
sys.path.insert(0, str(Path(__file__).parent))

import config
import portfolio as pf
import kite_auth
from kite_auth import get_kite

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-key-change-in-prod")
CORS(app)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def require_auth(f):
    """Decorator to check if user is authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "access_token" not in session:
            return jsonify({"error": "Not authenticated"}), 401
        return f(*args, **kwargs)
    return decorated


@app.route("/api/auth-status", methods=["GET"])
def auth_status():
    """Check if the user has an active access token."""
    has_token = "access_token" in session
    return jsonify({"authenticated": has_token})


@app.route("/api/login", methods=["POST"])
def login():
    """
    Kite interactive login. User opens the Kite login URL in browser,
    logs in, and pastes the request_token back here.
    POST body: {"request_token": "XXXXX"}
    """
    data = request.get_json()
    request_token = data.get("request_token")

    if not request_token:
        return jsonify({"error": "request_token required"}), 400

    try:
        kite = get_kite(force_login=False)
        api_secret = os.environ.get("KITE_API_SECRET")
        session_data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = session_data["access_token"]
        session["access_token"] = access_token
        log.info("User logged in via web app")
        return jsonify({"status": "success"})
    except Exception as e:
        log.error("Login failed: %s", e)
        return jsonify({"error": str(e)}), 400


@app.route("/api/logout", methods=["POST"])
def logout():
    """Clear the session."""
    session.clear()
    log.info("User logged out")
    return jsonify({"status": "success"})


@app.route("/api/login-url", methods=["GET"])
def login_url():
    """Get the Kite login URL to redirect the user to."""
    try:
        kite = get_kite(force_login=False)
        url = kite.login_url()
        return jsonify({"login_url": url})
    except Exception as e:
        log.error("Could not generate login URL: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/run-strategy", methods=["POST"])
@require_auth
def run_strategy():
    """
    Execute the full strategy: basket refresh, screener, entries, exits, orders.
    Returns a summary of what happened.
    """
    try:
        # Import here to avoid circular deps
        import strategy
        import main as main_module

        # Load state
        state = pf.load()

        # Get Kite client
        kite = get_kite(force_login=False)

        # Refresh basket if needed
        basket = strategy.refresh_basket_if_needed(kite, state)

        # Fetch technicals
        tech_by_symbol = {}
        for member in basket:
            sym = member["symbol"]
            try:
                import data_fetcher as dfetch
                df = dfetch.get_history(kite, sym, days=400)
                import indicators
                tech_by_symbol[sym] = indicators.technicals(df)
            except Exception as e:
                log.warning("Skipping %s: %s", sym, e)

        # Compute deployable liquidity
        import liquidity_buffer as lb
        lq_ltp = dfetch.get_ltp(kite, config.LIQUID_ETF)
        deployable = lb.deployable_liquidity(state, lq_ltp)

        # Current weights
        total_value = state["cash"] + state["liquidbees_units"] * lq_ltp
        holdings_value = {}
        for sym, tech in tech_by_symbol.items():
            units = pf.total_units(state, sym)
            if units > 0:
                holdings_value[sym] = units * tech["close"]
                total_value += holdings_value[sym]
        current_weights = (
            {s: v / total_value for s, v in holdings_value.items()}
            if total_value > 0
            else {}
        )

        # Run strategy
        decision = strategy.decide(state, basket, tech_by_symbol, current_weights, deployable)

        # Execute orders
        import order_engine
        order_engine.execute(kite, state, decision, tech_by_symbol)

        # Save state
        pf.save(state)

        # Telemetry
        total_after = state["cash"] + state["liquidbees_units"] * lq_ltp
        for sym, tech in tech_by_symbol.items():
            units = pf.total_units(state, sym)
            if units > 0:
                total_after += units * tech["close"]

        return jsonify({
            "status": "success",
            "portfolio_value": round(total_after, 2),
            "sells": decision["sells"],
            "sip_buys": decision["sip_buys"],
            "tactical_buys": decision["tactical_buys"],
            "ranked": [
                {
                    "symbol": r["symbol"],
                    "rank": r["rank_overall"],
                    "score": r["score"],
                    "category": r["category"],
                }
                for r in decision["ranked"]
            ],
        })

    except Exception as e:
        log.error("Strategy run failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/portfolio", methods=["GET"])
@require_auth
def portfolio():
    """Get current portfolio state: holdings, cash, war chest value."""
    try:
        state = pf.load()
        kite = get_kite(force_login=False)

        import data_fetcher as dfetch
        lq_ltp = dfetch.get_ltp(kite, config.LIQUID_ETF)

        holdings = []
        total_value = state["cash"] + state["liquidbees_units"] * lq_ltp

        for sym in state.get("positions", {}):
            units = pf.total_units(state, sym)
            if units <= 0:
                continue
            try:
                ltp = dfetch.get_ltp(kite, sym)
                cost = pf.avg_cost(state, sym)
                value = units * ltp
                pnl = value - (units * cost)
                pnl_pct = (pnl / (units * cost)) * 100 if cost > 0 else 0
                total_value += value
                holdings.append({
                    "symbol": sym,
                    "units": round(units, 4),
                    "avg_cost": round(cost, 2),
                    "ltp": round(ltp, 2),
                    "value": round(value, 2),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                })
            except Exception as e:
                log.warning("Could not fetch LTP for %s: %s", sym, e)

        return jsonify({
            "cash": round(state["cash"], 2),
            "liquidcase_units": round(state["liquidbees_units"], 4),
            "liquidcase_value": round(state["liquidbees_units"] * lq_ltp, 2),
            "holdings": holdings,
            "total_value": round(total_value, 2),
        })

    except Exception as e:
        log.error("Portfolio fetch failed: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/telemetry", methods=["GET"])
@require_auth
def telemetry():
    """Get recent equity history (last 30 runs)."""
    try:
        telemetry_file = Path(config.TELEMETRY_FILE)
        if not telemetry_file.exists():
            return jsonify({"data": []})

        import csv
        rows = []
        with open(telemetry_file) as f:
            reader = csv.DictReader(f)
            for row in list(reader)[-30:]:  # last 30
                rows.append({
                    "date": row["date"],
                    "total_value": float(row["total_value"]),
                })
        return jsonify({"data": rows})

    except Exception as e:
        log.error("Telemetry fetch failed: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    """Health check."""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
