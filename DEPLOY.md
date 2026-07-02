# Deploying the ETF Shop on a free-tier VM

This runs fine on the smallest box you can get. It does almost nothing 23h59m a
day — it wakes once each weekday morning, makes a handful of API calls, maybe
places one order, and sleeps. Either of these is plenty:

| Option | Free tier | Notes |
|---|---|---|
| **Oracle Cloud Always Free** | 1× Ampere A1 (up to 4 OCPU / 24 GB) or 2× AMD micro VMs, *forever* | Best choice — genuinely free indefinitely, generous specs. |
| **AWS EC2** | `t2.micro`/`t3.micro` free for 12 months | Fine, but bills after a year. Use only if you're already in AWS. |

Recommendation: **Oracle Always Free Ampere VM**. No 12-month cliff.

---

## 1. Spin up the VM

- Ubuntu 22.04 or 24.04 LTS.
- Open **no inbound ports** beyond SSH (22). This app makes only outbound calls.
- SSH in.

```bash
sudo timedatectl set-timezone Asia/Kolkata     # so the timer fires at IST
sudo apt update && sudo apt install -y python3-venv python3-pip git
```

## 2. Create a dedicated user (don't run as root)

```bash
sudo adduser --disabled-password --gecos "" etfshop
sudo su - etfshop
```

## 3. Get the code and install

```bash
# copy the etf_shop/ folder up (scp, git clone from your private repo, etc.)
cd ~/etf_shop
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 4. Secrets

**Local / quick start:**
```bash
cp .env.example .env
nano .env            # fill in keys
chmod 600 .env
```

**Production (recommended):** don't keep the password/TOTP in a plaintext file.

- *Oracle:* store them in **OCI Vault**, fetch in an `ExecStartPre` script that
  exports them into the environment for `main.py`.
- *AWS:* store in **SSM Parameter Store** (SecureString) and fetch with the
  instance role, e.g.
  ```bash
  export KITE_PASSWORD=$(aws ssm get-parameter --name /etfshop/kite_password \
        --with-decryption --query Parameter.Value --output text)
  ```
- Whatever you do, the TOTP secret on a server is a real exposure. If the box is
  compromised, someone can log into your broker. Keep the VM patched, SSH
  key-only, no open ports, and consider running **interactive auth daily** if you
  prefer not to store the 2FA secret at all (then you can't be fully unattended).

## 5. First login (interactive, once)

```bash
source venv/bin/activate
python kite_auth.py        # prints a URL; paste back the request_token
```
This proves your keys work and seeds `state/access_token.json`.

## 6. Validate before touching real money

```bash
# DRY_RUN is True by default in config.py — orders are logged, not placed.
python main.py
cat logs/run_*.log

# rough historical sanity check vs a plain SIP:
python backtest.py

# unit tests:
pytest tests/ -v
```

Paper-trade (DRY_RUN=True) for **at least one full market cycle** — you want to
watch the dip overlay fire in a real correction and confirm it behaves before you
trust it with capital. Only then set `DRY_RUN = False` in `config.py`.

## 7. Schedule it

```bash
sudo cp deploy/etfshop.service /etc/systemd/system/
sudo cp deploy/etfshop.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now etfshop.timer
systemctl list-timers etfshop.timer      # confirm next run time
```

Or, if you prefer cron over systemd:
```cron
45 9 * * 1-5 cd /home/etfshop/etf_shop && /home/etfshop/etf_shop/venv/bin/python main.py >> logs/cron.log 2>&1
```

## 8. Keep an eye on it

- `logs/run_YYYY-MM-DD.log` — what it did each day.
- `telemetry/equity_history.csv` — appended daily; your actual curve over time.
- `telemetry/trades_history.csv` — every intent/fill.
- Reconcile `state/portfolio.json` against `kite.holdings()` monthly. It's local
  bookkeeping, not the broker's truth.

## 9. Token-expiry reality

Kite access tokens die ~6am IST daily. With the TOTP env vars set, `main.py`
re-logs-in automatically each run. Without them, the 09:45 cron run can't
complete unattended — you'd need to run `python kite_auth.py` by hand first. Pick
your tradeoff: full automation (store TOTP secret, accept the risk) vs. a daily
30-second manual login (no secret on disk).
