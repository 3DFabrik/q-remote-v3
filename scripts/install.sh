#!/usr/bin/env bash
# Q-Remote V3 – Raspberry Pi installer (deps + venv + systemd service)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="q-remote"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

SERVICE_USER="${SUDO_USER:-$(stat -c '%U' "$REPO_ROOT")}"
HOST="0.0.0.0"
PORT="8080"
INSTALL_SERVICE=1
START_SERVICE=1

usage() {
    cat <<'EOF'
Usage: sudo ./scripts/install.sh [options]

Installs system packages, Python venv + pip deps, optional admin user,
and a systemd service for Q-Remote V3.

Options:
  --user NAME    Linux user to run the service (default: repo owner)
  --port PORT    HTTP port (default: 8080)
  --deps-only    Skip systemd service (venv + packages only)
  --no-start     Install service but do not enable/start it
  -h, --help     Show this help

Examples:
  sudo ./scripts/install.sh
  sudo ./scripts/install.sh --user pi --port 8080
  ./scripts/install.sh --deps-only
EOF
}

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

run_as_service_user() {
    if [[ "$(id -u)" -eq 0 ]]; then
        sudo -u "$SERVICE_USER" -H bash -lc "cd '$REPO_ROOT' && $*"
    else
        bash -lc "cd '$REPO_ROOT' && $*"
    fi
}

run_as_service_user_with_env() {
    if [[ "$(id -u)" -eq 0 ]]; then
        sudo -u "$SERVICE_USER" -H \
            ADMIN_USER="$ADMIN_USER" ADMIN_PASS="$ADMIN_PASS" \
            bash -lc "cd '$REPO_ROOT' && $*"
    else
        ADMIN_USER="$ADMIN_USER" ADMIN_PASS="$ADMIN_PASS" \
            bash -lc "cd '$REPO_ROOT' && $*"
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user) SERVICE_USER="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --deps-only) INSTALL_SERVICE=0; shift ;;
        --no-start) START_SERVICE=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1 (try --help)" ;;
    esac
done

if [[ ! -f "$REPO_ROOT/backend/main.py" ]]; then
    die "Run this script from a cloned q-remote-v3 repository."
fi

id "$SERVICE_USER" &>/dev/null || die "User not found: $SERVICE_USER"

if [[ "$INSTALL_SERVICE" -eq 1 ]] && [[ "$(id -u)" -ne 0 ]]; then
    die "Installing the systemd service requires root. Re-run with sudo or use --deps-only."
fi

if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    log "Detected: ${PRETTY_NAME:-Linux}"
fi

APT_PACKAGES=(
    python3
    python3-venv
    python3-pip
    alsa-utils
    git
)

if [[ "$(id -u)" -eq 0 ]]; then
    log "Installing system packages: ${APT_PACKAGES[*]}"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${APT_PACKAGES[@]}"

    for grp in gpio dialout audio; do
        if getent group "$grp" &>/dev/null; then
            usermod -aG "$grp" "$SERVICE_USER" 2>/dev/null || true
        fi
    done
else
    log "Skipping apt (not root). Ensure these are installed: ${APT_PACKAGES[*]}"
fi

log "Creating Python virtualenv in $REPO_ROOT/venv"
rm -rf "$REPO_ROOT/venv"
run_as_service_user "python3 -m venv venv"
run_as_service_user "venv/bin/pip install --upgrade pip wheel"
run_as_service_user "venv/bin/pip install -r requirements.txt"

log "Verifying Python imports"
run_as_service_user "venv/bin/python -c \"from backend.main import asgi_app; print('import ok')\""

if [[ ! -f "$REPO_ROOT/config.local.yaml" ]]; then
    if [[ -f "$REPO_ROOT/config.local.yaml.example" ]]; then
        cp "$REPO_ROOT/config.local.yaml.example" "$REPO_ROOT/config.local.yaml"
        log "Created config.local.yaml from example (edit radio device if needed)"
    fi
fi

mkdir -p "$REPO_ROOT/logs" "$REPO_ROOT/backups"
chown -R "$SERVICE_USER:$SERVICE_USER" "$REPO_ROOT/logs" "$REPO_ROOT/backups" 2>/dev/null || true

USERS_FILE="$REPO_ROOT/users.json"
if [[ ! -f "$USERS_FILE" ]]; then
    log "No users.json found — create the first admin account"
    read -r -p "Admin callsign: " ADMIN_USER
    read -r -s -p "Admin password: " ADMIN_PASS
    echo
    [[ -n "$ADMIN_USER" && -n "$ADMIN_PASS" ]] || die "Callsign and password are required"

    ADMIN_USER="$(echo "$ADMIN_USER" | tr '[:lower:]' '[:upper:]')"
    run_as_service_user_with_env "venv/bin/python scripts/create_admin.py"
else
    log "users.json already exists — keeping current accounts"
fi

if [[ "$INSTALL_SERVICE" -eq 0 ]]; then
    log "Deps-only install complete."
    log "Run manually: cd $REPO_ROOT && source venv/bin/activate && uvicorn backend.main:asgi_app --host $HOST --port $PORT"
    exit 0
fi

log "Installing systemd unit: $SERVICE_FILE"
sed \
    -e "s|@INSTALL_DIR@|$REPO_ROOT|g" \
    -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
    -e "s|@HOST@|$HOST|g" \
    -e "s|@PORT@|$PORT|g" \
    "$REPO_ROOT/scripts/q-remote.service.in" > "$SERVICE_FILE"

systemctl daemon-reload

if [[ "$START_SERVICE" -eq 1 ]]; then
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log "Service is running"
    else
        printf 'WARN: Service failed to start. Check: journalctl -u %s -n 30\n' "$SERVICE_NAME" >&2
    fi
else
    log "Service installed but not started (--no-start)"
fi

cat <<EOF

Q-Remote V3 installed.

  Service : systemctl status $SERVICE_NAME
  Logs    : journalctl -u $SERVICE_NAME -f
  URL     : http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT
  Config  : $REPO_ROOT/config.local.yaml
  Users   : $REPO_ROOT/users.json

HTTPS (required for microphone) — put Caddy or nginx in front of port $PORT.

EOF
