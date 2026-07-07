# ETF Shop — Momentum Rotation Engine

A delivery-only, tax-aware, multi-ETF momentum rotation system for Kite Connect (Zerodha). Runs `DRY_RUN = True` by default — no real orders until you explicitly flip that flag in `config.py`.

## How to run

The **Start application** workflow runs `python app.py`, which starts the Flask dashboard on port 5000.

To use the dashboard:
1. Click **"Click here to log in with Zerodha"** — this opens the Kite Connect OAuth flow
2. After logging in, Zerodha redirects you to a URL containing `request_token=XXXXX`
3. Copy that token and paste it into the dashboard's input field, then click **Log In**
4. You're now authenticated — use the dashboard to run the strategy, view portfolio, and check telemetry

Access tokens expire each morning (~6am IST), so you'll need to re-authenticate daily.

## Unattended / headless login

If `KITE_USER_ID`, `KITE_PASSWORD`, and `KITE_TOTP_SECRET` are all set (they are), running `python kite_auth.py` from the Shell will authenticate automatically via TOTP and save the token to `state/access_token.json`. The dashboard reuses this token if it's fresh.

## Key files

| File | Purpose |
|------|---------|
| `app.py` | Flask web dashboard + API endpoints |
| `main.py` | CLI entry point for headless daily runs |
| `config.py` | All strategy parameters (knobs live here) |
| `kite_auth.py` | Authentication (interactive + unattended TOTP) |
| `strategy.py` | Core buy/sell decision logic |
| `backtest.py` | Offline sanity-check backtester |
| `candidates.csv` | NSE ETF universe (starter list — verify symbols) |

## State and data directories

| Directory | Contents |
|-----------|---------|
| `state/` | `portfolio.json`, saved access token |
| `logs/` | Per-run strategy logs |
| `telemetry/` | `equity_history.csv`, `trades_history.csv` |

## Secrets configured

- `KITE_API_KEY` / `KITE_API_SECRET` — Kite Connect app credentials
- `KITE_USER_ID` / `KITE_PASSWORD` / `KITE_TOTP_SECRET` — unattended login
- `SESSION_SECRET` — Flask session key

## Safety

`DRY_RUN = True` in `config.py` — flip to `False` only after paper-trading through at least one real market correction.

## User preferences

- Keep the existing project structure and stack (Python/Flask/Kite Connect)
