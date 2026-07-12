#!/usr/bin/env bash
set -euo pipefail

# ───────────────────────────────────────────────────────────
# HermesClawZero — macOS One-Click Setup
# ───────────────────────────────────────────────────────────
REPO="SunMe1977/HermesClawZero-ConfigSidecar"
REPO_URL="https://github.com/$REPO.git"
DIR="${DIR:-$HOME/HermesClawZero-ConfigSidecar}"
DASHBOARD_PORT="${PORT:-8010}"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()  { printf "${GREEN}✓${NC} %s\n" "$1"; }
warn()  { printf "${CYAN}→${NC} %s\n" "$1"; }
err()   { printf "${RED}✗${NC} %s\n" "$1"; }
header(){ printf "\n${BOLD}%s${NC}\n" "$1"; }

# OSC 8 hyperlink — clickable in modern terminals (iTerm2, kitty, Terminal.app)
clickable() { printf "\e]8;;%s\a%s\e]8;;\a" "$1" "$2"; }

# ── Step 0: macOS check ──
header "🍎 HermesClawZero — macOS Setup"
if [[ "$(uname)" != "Darwin" ]]; then
    err "This script is for macOS only."
    exit 1
fi

# ── Step 1: Homebrew ──
header "1/5  Checking Homebrew"
if command -v brew &>/dev/null; then
    info "Homebrew $(brew --version | head -1)"
else
    warn "Installing Homebrew from https://brew.sh …"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add to PATH for Apple Silicon
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    info "Homebrew installed"
fi

# ── Step 2: Docker ──
header "2/5  Checking Docker"
if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    info "Docker $(docker --version)"
else
    warn "Docker Desktop not running — installing via Homebrew …"
    brew install --cask docker
    warn "Starting Docker Desktop (may take a moment)…"
    open -a Docker
    for i in $(seq 1 60); do
        if docker info &>/dev/null 2>&1; then
            info "Docker Desktop ready"
            break
        fi
        sleep 2
    done
    if ! docker info &>/dev/null 2>&1; then
        err "Docker Desktop did not start in time. Launch it manually, then re-run this script."
        exit 1
    fi
fi

# ── Step 3: Clone / Pull repo ──
header "3/5  Cloning repository"
if [[ -d "$DIR" ]]; then
    warn "Directory $DIR exists — pulling latest…"
    git -C "$DIR" pull --ff-only
else
    git clone "$REPO_URL" "$DIR"
fi
info "Repository at $DIR"
cd "$DIR"

# ── Step 4: .env ──
header "4/5  Configuring environment"
if [[ ! -f .env ]]; then
    cp .env.example .env
    # Generate random credentials
    if [[ "$(uname)" == "Darwin" ]]; then
        GEN_KEY="$(uuidgen | md5 | head -c 32)"
        GEN_DB="$(uuidgen | md5 | head -c 16)"
        GEN_DASH="$(uuidgen | md5 | head -c 16)"
    else
        GEN_KEY="$(date +%s | md5sum | head -c 32)"
        GEN_DB="$(date +%s | md5sum | head -c 16)"
        GEN_DASH="$(date +%s | md5sum | head -c 16)"
    fi
    if [[ "$(uname)" == "Darwin" ]]; then
        sed -i '' "s/your_secret_api_key_here/$GEN_KEY/" .env
        sed -i '' "s/your_db_password_here/$GEN_DB/" .env
        sed -i '' "s/change_this_dashboard_password/$GEN_DASH/" .env
    else
        sed -i "s/your_secret_api_key_here/$GEN_KEY/" .env
        sed -i "s/your_db_password_here/$GEN_DB/" .env
        sed -i "s/change_this_dashboard_password/$GEN_DASH/" .env
    fi
    info ".env created with random credentials"
else
    warn ".env already exists — using it as-is"
fi

# ── Step 5: Docker Compose ──
header "5/5  Starting HermesClawZero"
docker compose --profile ollama up -d --build

# Wait for health
printf "\n  Waiting for API…"
for i in $(seq 1 30); do
    if curl -sf http://localhost:$DASHBOARD_PORT/healthz >/dev/null 2>&1; then
        echo ""; info "API ready"
        break
    fi
    printf "."
    sleep 2
done
if ! curl -sf http://localhost:$DASHBOARD_PORT/healthz >/dev/null 2>&1; then
    err "API did not become healthy. Check: docker compose logs api"
    exit 1
fi

# ── Done ──
header "✅ HermesClawZero is running"

# Dashboard URL (clickable)
DASH_URL="http://localhost:$DASHBOARD_PORT/dashboard"
HEALTH_URL="http://localhost:$DASHBOARD_PORT/healthz"

printf "  ${BOLD}Dashboard:${NC}  "
clickable "$DASH_URL" "$DASH_URL"
echo ""
printf "  ${BOLD}Health:${NC}     "
clickable "$HEALTH_URL" "$HEALTH_URL"
echo ""
echo ""
printf "  Credentials stored in: $(clickable "file://$DIR/.env" "$DIR/.env")\n"
printf "  Dashboard login:       admin / (see DASHBOARD_PASSWORD in .env)\n"
echo ""
printf "  ${CYAN}Run ${BOLD}docker compose logs -f api${NC} to follow logs\n"
