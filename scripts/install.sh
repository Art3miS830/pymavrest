#!/bin/bash

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

log() {
    echo -e "${GREEN}$1${NC}"
}

error() {
    echo -e "${RED}ERROR: $1${NC}" >&2
    exit 1
}

usage() {
    cat <<EOF
Install/update pymavrest dependencies and optionally deploy production service.

Usage:
  $0 [options]

Options:
  --branch <name>            Git branch to clone/pull from.
                             Default: master

  --app-dir <path>           Target directory for the repository.
                             Default: \$HOME/app

  --prod <true|false>        Post-install behavior.
                             true  -> deploy and run as systemd service
                             false -> install only (no app start)
                             Accepted: true,false,1,0,yes,no
                             Default: true

  --backend <name>           Telemetry backend passed to main.py.
                             Accepted: pymavlink, mavsdk
                             Default: pymavlink

  -h, --help                 Show this help and exit.

Examples:
  $0
  $0 --prod false
  $0 --backend mavsdk
  $0 --branch master --app-dir /home/pi/app --prod true --backend pymavlink
EOF
}

BRANCH="master"
APP_DIR="${HOME}/app"
RUN_PROD="true"
BACKEND="pymavlink"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch)
            [[ $# -ge 2 ]] || error "--branch requires a value"
            BRANCH="$2"
            shift 2
            ;;
        --app-dir)
            [[ $# -ge 2 ]] || error "--app-dir requires a value"
            APP_DIR="$2"
            shift 2
            ;;
        --prod)
            [[ $# -ge 2 ]] || error "--prod requires a value: true|false"
            case "${2,,}" in
                true|1|yes)
                    RUN_PROD="true"
                    ;;
                false|0|no)
                    RUN_PROD="false"
                    ;;
                *)
                    error "Invalid --prod value: $2 (expected true|false)"
                    ;;
            esac
            shift 2
            ;;
        --backend)
            [[ $# -ge 2 ]] || error "--backend requires a value: pymavlink|mavsdk"
            case "${2,,}" in
                pymavlink|mavsdk)
                    BACKEND="${2,,}"
                    ;;
                *)
                    error "Invalid --backend value: $2 (expected pymavlink|mavsdk)"
                    ;;
            esac
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            ;;
    esac
done

if [[ "${EUID}" -eq 0 ]]; then
    SUDO=""
    SERVICE_USER="root"
    SERVICE_GROUP="root"
    SERVICE_HOME="/root"
else
    command -v sudo >/dev/null 2>&1 || error "sudo is required for non-root installation"
    SUDO="sudo"
    SERVICE_USER="${USER}"
    SERVICE_GROUP="$(id -gn)"
    SERVICE_HOME="${HOME}"
fi

log "Starting installation..."

log "Installing system dependencies..."
$SUDO apt update
$SUDO apt install -y wget curl git libspatialindex-dev build-essential python3-dev

log "Checking uv installation..."
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="${HOME}/.local/bin:${PATH}"
command -v uv >/dev/null 2>&1 || error "uv installation failed"

log "Ensuring Python 3.12 is installed via uv..."
uv python install 3.12

REPO_URL="https://github.com/hadif1999/pymavrest.git"
PARENT_DIR="$(dirname "${APP_DIR}")"
mkdir -p "${PARENT_DIR}"

if [[ -d "${APP_DIR}/.git" ]]; then
    log "Updating existing repository in ${APP_DIR}..."
    git -C "${APP_DIR}" fetch --prune origin
    git -C "${APP_DIR}" checkout "${BRANCH}"
    git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"
elif [[ -e "${APP_DIR}" ]]; then
    error "${APP_DIR} exists but is not a git repository"
else
    log "Cloning repository (${BRANCH}) into ${APP_DIR}..."
    git clone -b "${BRANCH}" "${REPO_URL}" "${APP_DIR}" || error "Failed to clone branch '${BRANCH}'"
fi

log "Installing Python dependencies..."
cd "${APP_DIR}"
uv sync

if [[ "${RUN_PROD}" == "true" ]]; then
    log "Deploying systemd service (production mode)..."
    SERVICE_DEST="/etc/systemd/system/pymavrest.service"
    $SUDO tee "${SERVICE_DEST}" >/dev/null <<EOF
[Unit]
Description=mavlink restapi service
After=network-online.target
Wants=network-online.target

[Service]
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${APP_DIR}
ExecStart=${SERVICE_HOME}/.local/bin/uv run -p 3.12 ${APP_DIR}/main.py -c config.json --backend ${BACKEND}
Restart=always
Environment=PYTHONUNBUFFERED=1
Environment=PATH=${SERVICE_HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    $SUDO systemctl daemon-reload
    $SUDO systemctl enable --now pymavrest.service
    $SUDO systemctl restart pymavrest.service

    log "Installation complete. Service: pymavrest.service"
else
    log "Install-only mode selected: service deployment skipped."
    log "Run manually when needed:"
    log "  cd ${APP_DIR} && uv run -p 3.12 main.py --backend ${BACKEND}"
fi
