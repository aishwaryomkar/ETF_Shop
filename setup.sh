#!/usr/bin/env bash
# setup.sh — run once after git clone on your Oracle Cloud VM.
# Handles three things:
#   1. Installs Python 3.9 if the system Python is too old (< 3.7)
#   2. Creates the venv with the right Python and installs requirements
#   3. Creates .env interactively (the file that is never committed to git)
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

# -----------------------------------------------------------------------
# Colour helpers
# -----------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}  $*"; }
err()   { echo -e "${RED}[error]${NC} $*"; exit 1; }

# -----------------------------------------------------------------------
# 1. Find a usable Python (>= 3.7 required for from __future__ annotations +
#    pandas 1.3; Python 3.9 preferred since it's in Oracle Linux repos)
# -----------------------------------------------------------------------
find_python() {
    for cmd in python3.11 python3.10 python3.9 python3.8 python3.7 python3; do
        if command -v "$cmd" &>/dev/null; then
            ver=$("$cmd" -c "import sys; print(sys.version_info[:2])")
            if "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3,7) else 1)" 2>/dev/null; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON=$(find_python || true)

if [ -z "$PYTHON" ]; then
    warn "No Python >= 3.7 found. Attempting to install Python 3.9..."

    if command -v dnf &>/dev/null; then
        # Oracle Linux 8 / RHEL 8 / AlmaLinux 8
        sudo dnf install -y python39 python39-pip python39-devel || \
            err "dnf install python39 failed. Run manually: sudo dnf install python39"
        PYTHON=python3.9

    elif command -v yum &>/dev/null; then
        # Oracle Linux 7 / RHEL 7 — needs EPEL or SCL
        warn "Oracle Linux 7 detected. Installing via Software Collections..."
        sudo yum install -y centos-release-scl 2>/dev/null || true
        sudo yum install -y rh-python39 2>/dev/null || \
            err "Could not install Python 3.9 via SCL. Try: sudo yum install python39 or build from source."
        source /opt/rh/rh-python39/enable
        PYTHON=python3.9

    elif command -v apt-get &>/dev/null; then
        # Ubuntu (fallback)
        sudo apt-get update -q
        sudo apt-get install -y python3.9 python3.9-venv python3.9-dev || \
            err "apt install python3.9 failed."
        PYTHON=python3.9

    else
        err "Cannot detect package manager. Install Python 3.9 manually, then re-run this script."
    fi
fi

PY_VER=$("$PYTHON" -c "import sys; print('.'.join(map(str,sys.version_info[:3])))")
info "Using Python $PY_VER at $(command -v $PYTHON)"

# -----------------------------------------------------------------------
# 2. Create venv and install requirements
# -----------------------------------------------------------------------
VENV_DIR="$REPO_DIR/venv"

if [ -d "$VENV_DIR" ]; then
    warn "venv/ already exists — skipping creation. Delete it and re-run if you want a fresh install."
else
    info "Creating venv at $VENV_DIR..."
    "$PYTHON" -m venv "$VENV_DIR" || \
        err "venv creation failed. Try: sudo dnf install python39-devel"
fi

PIP="$VENV_DIR/bin/pip"
info "Upgrading pip inside the venv..."
"$PIP" install --upgrade pip --quiet

info "Installing requirements..."
"$PIP" install -r "$REPO_DIR/requirements.txt" --quiet
info "Requirements installed."

# -----------------------------------------------------------------------
# 3. Create .env — secrets are NEVER committed to git, so they must be
#    written on the server after cloning. This prompts you for each value
#    and writes them to .env with 0600 permissions.
# -----------------------------------------------------------------------
ENV_FILE="$REPO_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    warn ".env already exists. Skipping. Edit it manually if you need to update keys: nano $ENV_FILE"
else
    info ""
    info "--- Creating .env (secrets file, never committed to git) ---"
    info "Leave a field blank and press Enter to skip it (e.g. if you plan to use interactive login)."
    info ""

    read -rp "  KITE_API_KEY     : " KITE_API_KEY
    read -rsp "  KITE_API_SECRET  : " KITE_API_SECRET; echo
    read -rp "  KITE_USER_ID     : " KITE_USER_ID
    read -rsp "  KITE_PASSWORD    : " KITE_PASSWORD; echo
    info ""
    info "  KITE_TOTP_SECRET is the BASE32 secret behind your authenticator QR code,"
    info "  NOT the 6-digit code. Find it in Zerodha's 2FA settings (you may need to"
    info "  reset 2FA to reveal it). Leave blank to use interactive login instead."
    read -rsp "  KITE_TOTP_SECRET : " KITE_TOTP_SECRET; echo

    cat > "$ENV_FILE" <<EOF
KITE_API_KEY=${KITE_API_KEY}
KITE_API_SECRET=${KITE_API_SECRET}
KITE_USER_ID=${KITE_USER_ID}
KITE_PASSWORD=${KITE_PASSWORD}
KITE_TOTP_SECRET=${KITE_TOTP_SECRET}
EOF

    chmod 600 "$ENV_FILE"
    info ".env written to $ENV_FILE (permissions: 0600 — readable only by you)."
fi

# -----------------------------------------------------------------------
# 4. Quick sanity check
# -----------------------------------------------------------------------
info ""
info "--- Sanity check ---"
"$VENV_DIR/bin/python" -c "
import sys, pandas as pd
print(f'Python  : {sys.version}')
print(f'pandas  : {pd.__version__}')
print('Import check passed.')
"

info ""
info "=== Setup complete. Next steps: ==="
info ""
info "  1. Activate the venv in your current shell:"
info "       source venv/bin/activate"
info ""
info "  2. Run tests:"
info "       pytest tests/ -v"
info ""
info "  3. Bootstrap your existing ETF holdings:"
info "       python bootstrap_holdings.py"
info "       python bootstrap_holdings.py --apply"
info ""
info "  4. Dry-run (DRY_RUN=True in config.py — default):"
info "       python kite_auth.py    # one-time interactive login to seed the token"
info "       python main.py"
info ""
info "  5. Schedule (after you're happy with dry-run output):"
info "       sudo cp deploy/etfshop.service deploy/etfshop.timer /etc/systemd/system/"
info "       sudo systemctl daemon-reload"
info "       sudo systemctl enable --now etfshop.timer"
info ""
