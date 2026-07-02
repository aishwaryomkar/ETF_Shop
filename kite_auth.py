from __future__ import annotations
"""
kite_auth.py — daily Kite Connect authentication.

Kite access tokens die every morning (~6am IST), so a headless server needs to
re-authenticate before each run. Two modes:

  1. Interactive (kite_auth.py run by hand): prints the login URL, you paste the
     request_token back. Use this the first time / for local testing.

  2. Unattended (automatic, for the EC2/Oracle cron): uses the TOTP secret to
     complete 2FA programmatically with pyotp — no human in the loop.

SECURITY: unattended mode requires your Kite password and TOTP secret on the
box. That is a real risk decision. Mitigations used here:
  - secrets are read from environment variables, never hardcoded;
  - on a server, set them via a secrets manager (AWS SSM Parameter Store /
    Oracle Vault) injected at runtime, not a plaintext .env committed anywhere;
  - the saved access_token file is written 0600 (owner read/write only).

Env vars expected:
  KITE_API_KEY, KITE_API_SECRET           (always)
  KITE_USER_ID, KITE_PASSWORD, KITE_TOTP_SECRET   (unattended only)
"""

import os
import json
import stat
import time
import logging
import pathlib

from kiteconnect import KiteConnect

log = logging.getLogger("kite_auth")


def _load_dotenv() -> None:
    """Auto-load .env from the repo root. Safe if the file doesn't exist.
    Does NOT overwrite env vars already set in the shell (real env wins)."""
    env_path = pathlib.Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if key and key not in os.environ:
                os.environ[key] = val

_load_dotenv()

TOKEN_FILE = "state/access_token.json"


def _save_token(access_token: str) -> None:
    os.makedirs("state", exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump({"access_token": access_token, "ts": time.time()}, f)
    os.chmod(TOKEN_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600


def _load_token() -> str | None:
    try:
        with open(TOKEN_FILE) as f:
            data = json.load(f)
        # tokens are valid for the trading day; treat anything from today as live
        if time.time() - data["ts"] < 16 * 3600:
            return data["access_token"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        pass
    return None


def _interactive_login(kite: KiteConnect) -> str:
    print("\n  Open this URL, log in, and copy the request_token from the redirect:")
    print(" ", kite.login_url(), "\n")
    request_token = input("  request_token: ").strip()
    api_secret = os.environ["KITE_API_SECRET"]
    session = kite.generate_session(request_token, api_secret=api_secret)
    return session["access_token"]


def _unattended_login(kite: KiteConnect) -> str:
    """Headless login via Selenium-free HTTP flow + pyotp 2FA."""
    import pyotp
    import requests

    user_id = os.environ["KITE_USER_ID"]
    password = os.environ["KITE_PASSWORD"]
    totp_secret = os.environ["KITE_TOTP_SECRET"]
    api_secret = os.environ["KITE_API_SECRET"]

    s = requests.Session()
    # Step 1: password login
    r = s.post("https://kite.zerodha.com/api/login",
               data={"user_id": user_id, "password": password})
    r.raise_for_status()
    request_id = r.json()["data"]["request_id"]

    # Step 2: TOTP 2FA
    totp = pyotp.TOTP(totp_secret).now()
    r = s.post("https://kite.zerodha.com/api/twofa",
               data={"user_id": user_id, "request_id": request_id,
                     "twofa_value": totp, "twofa_type": "totp"})
    r.raise_for_status()

    # Step 3: hit the Connect login URL; the redirect carries request_token
    try:
        s.get(kite.login_url(), allow_redirects=True)
    except Exception:
        pass
    # the request_token lands in the final redirect URL query string
    final = s.get(f"https://kite.zerodha.com/connect/login"
                  f"?api_key={os.environ['KITE_API_KEY']}&v=3",
                  allow_redirects=True)
    # parse request_token from history
    request_token = None
    for resp in (*final.history, final):
        if "request_token=" in resp.url:
            request_token = resp.url.split("request_token=")[1].split("&")[0]
            break
    if not request_token:
        raise RuntimeError("Could not extract request_token — Kite may have "
                           "changed the login flow, or 2FA failed.")

    session = kite.generate_session(request_token, api_secret=api_secret)
    return session["access_token"]


def get_kite(force_login: bool = False) -> KiteConnect:
    """Return an authenticated KiteConnect client.

    Reuses today's saved token if present; otherwise logs in. Picks unattended
    mode automatically when the TOTP env vars are set, else falls back to
    interactive.
    """
    api_key = os.environ["KITE_API_KEY"]
    kite = KiteConnect(api_key=api_key)

    if not force_login:
        tok = _load_token()
        if tok:
            kite.set_access_token(tok)
            try:
                kite.profile()  # cheap call to confirm the token is alive
                log.info("Reusing saved access token.")
                return kite
            except Exception:
                log.info("Saved token dead, re-authenticating.")

    if os.environ.get("KITE_TOTP_SECRET"):
        log.info("Unattended TOTP login.")
        access_token = _unattended_login(kite)
    else:
        log.info("Interactive login.")
        access_token = _interactive_login(kite)

    kite.set_access_token(access_token)
    _save_token(access_token)
    return kite


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    k = get_kite(force_login=True)
    print("Logged in as:", k.profile()["user_name"])
