#!/bin/bash
# ============================================================
# ng1-f5-emulator-netscaler — Interactive Installer
# ============================================================
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${GREEN}✅ $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠  $*${NC}"; }
error() { echo -e "${RED}❌ $*${NC}"; exit 1; }
step()  { echo -e "\n${BLUE}${BOLD}── $* ──${NC}"; }
ask()   { echo -e "${BOLD}$*${NC}"; }

echo -e "${BOLD}"
echo "╔══════════════════════════════════════════════════════╗"
echo "║       ng1-f5-emulator-netscaler  —  Installer             ║"
echo "║   F5 iControl REST emulator for nGeniusONE           ║"
echo "╚══════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Step 1: OS detection ──────────────────────────────────────────
step "Step 1 — System check"

# Architecture
ARCH=$(uname -m)
if [[ "$ARCH" != "x86_64" && "$ARCH" != "aarch64" ]]; then
    warn "Unsupported architecture: $ARCH (expected x86_64 or aarch64)"
fi
info "Architecture: $ARCH"

# OS detection
OS_NAME="unknown"
OS_VER="unknown"
PKG_MGR=""

if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_NAME="${ID:-unknown}"
    OS_VER="${VERSION_ID:-unknown}"
fi

case "$OS_NAME" in
    ubuntu|debian)              PKG_MGR="apt" ;;
    rhel|centos|rocky|almalinux) PKG_MGR="yum" ;;
    fedora)                     PKG_MGR="dnf" ;;
    *)                          PKG_MGR="unknown" ;;
esac

info "OS: $OS_NAME $OS_VER  |  Package manager: $PKG_MGR"

if [ "$PKG_MGR" == "unknown" ]; then
    warn "Unknown distro — you may need to install packages manually"
fi

# Root / sudo check
if [ "$EUID" -ne 0 ] && ! sudo -n true 2>/dev/null; then
    warn "Not running as root and no passwordless sudo — some installs may fail"
fi

# ── Step 2: Prerequisites ─────────────────────────────────────────
step "Step 2 — Prerequisites"

install_pkg() {
    local pkg=$1
    local friendly=$2
    local install_cmd=$3

    if eval "$4" &>/dev/null 2>&1; then
        info "$friendly — already installed"
        return 0
    fi

    warn "$friendly not found"
    ask "Install $friendly now? [Y/n]: "
    read -r REPLY
    if [[ "${REPLY:-Y}" =~ ^[Yy]$ ]]; then
        echo "Installing $friendly..."
        eval "$install_cmd" || { error "Failed to install $friendly. Install manually and re-run."; }
        info "$friendly installed"
    else
        error "$friendly is required. Install it manually and re-run."
    fi
}

# curl
install_pkg "curl" "curl" \
    "$([ "$PKG_MGR" == "apt" ] && echo 'sudo apt-get install -y curl' || echo 'sudo $PKG_MGR install -y curl')" \
    "command -v curl"

# openssl (for cert generation)
install_pkg "openssl" "OpenSSL" \
    "$([ "$PKG_MGR" == "apt" ] && echo 'sudo apt-get install -y openssl' || echo 'sudo $PKG_MGR install -y openssl')" \
    "command -v openssl"

# Docker
if ! command -v docker &>/dev/null; then
    warn "Docker not found"
    ask "Install Docker now? [Y/n]: "
    read -r REPLY
    if [[ "${REPLY:-Y}" =~ ^[Yy]$ ]]; then
        echo "Installing Docker via official install script..."
        curl -fsSL https://get.docker.com | sh
        sudo usermod -aG docker "$USER" 2>/dev/null || true
        info "Docker installed — you may need to log out and back in for group membership"
    else
        error "Docker is required. Install from https://docs.docker.com/get-docker/ and re-run."
    fi
else
    info "Docker $(docker --version | awk '{print $3}' | tr -d ',')"
fi

# Docker daemon running
if ! docker info &>/dev/null; then
    warn "Docker daemon not running"
    ask "Start Docker daemon now? [Y/n]: "
    read -r REPLY
    if [[ "${REPLY:-Y}" =~ ^[Yy]$ ]]; then
        sudo systemctl start docker && sudo systemctl enable docker
        sleep 3
        docker info &>/dev/null || error "Docker daemon failed to start. Check: sudo systemctl status docker"
        info "Docker daemon started"
    else
        error "Docker daemon must be running. Start it with: sudo systemctl start docker"
    fi
fi

# Docker Compose v2
if ! docker compose version &>/dev/null 2>&1; then
    warn "Docker Compose v2 not found"
    ask "Install Docker Compose plugin now? [Y/n]: "
    read -r REPLY
    if [[ "${REPLY:-Y}" =~ ^[Yy]$ ]]; then
        if [ "$PKG_MGR" == "apt" ]; then
            sudo apt-get install -y docker-compose-plugin
        else
            # Manual install
            COMPOSE_VER="v2.24.5"
            sudo mkdir -p /usr/local/lib/docker/cli-plugins
            sudo curl -SL "https://github.com/docker/compose/releases/download/${COMPOSE_VER}/docker-compose-linux-x86_64" \
                -o /usr/local/lib/docker/cli-plugins/docker-compose
            sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
        fi
        docker compose version &>/dev/null || error "Docker Compose install failed"
        info "Docker Compose $(docker compose version --short)"
    else
        error "Docker Compose v2 is required. Install via: sudo apt-get install docker-compose-plugin"
    fi
else
    info "Docker Compose $(docker compose version --short)"
fi

# ── Step 3: Port availability ─────────────────────────────────────
step "Step 3 — Port check"

check_port() {
    local port=$1
    local name=$2
    if ss -tlnp 2>/dev/null | grep -q ":$port " || netstat -tlnp 2>/dev/null | grep -q ":$port "; then
        warn "Port $port already in use — $name may conflict"
        ask "Continue anyway? [y/N]: "
        read -r REPLY
        [[ "${REPLY}" =~ ^[Yy]$ ]] || error "Free port $port and re-run"
    else
        info "Port $port — available"
    fi
}

check_port 8446 "tmos-shim-netscaler"

# ── Step 4: Disk space ────────────────────────────────────────────
step "Step 4 — Disk space"
AVAIL_MB=$(df -m . | awk 'NR==2 {print $4}')
if [ "$AVAIL_MB" -lt 500 ]; then
    warn "Low disk space: ${AVAIL_MB}MB available (500MB recommended)"
    ask "Continue anyway? [y/N]: "
    read -r REPLY
    [[ "${REPLY}" =~ ^[Yy]$ ]] || error "Free up disk space and re-run"
else
    info "Disk space: ${AVAIL_MB}MB available"
fi

# ── Step 5: Detect host IP ────────────────────────────────────────
step "Step 5 — Network"
DETECTED_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
[ -z "$DETECTED_IP" ] && DETECTED_IP=$(hostname -I | awk '{print $1}')
ask "Host IP detected as ${BOLD}$DETECTED_IP${NC}. Press Enter to accept or type a different IP:"
read -r INPUT_IP
HOST_IP="${INPUT_IP:-$DETECTED_IP}"
info "Using host IP: $HOST_IP"

# ── Step 6: nG1 credentials ───────────────────────────────────────
step "Step 6 — nGeniusONE credentials"
echo "These are the credentials you will enter in nG1 BIG-IP Discovery."
ask "Username [admin]: "
read -r NG1_USER; NG1_USER="${NG1_USER:-admin}"
ask "Password [netscout1]: "
read -r NG1_PASS; NG1_PASS="${NG1_PASS:-netscout1}"
info "nG1 credentials: $NG1_USER / $NG1_PASS"

# ── Step 7: Backend selection ─────────────────────────────────────
step "Step 7 — Backend adapter"
info "Using NetScaler adapter (port 8446)"
ENABLE_A10=true

# ── Step 8: A10 config ────────────────────────────────────────────
if $ENABLE_A10; then
    step "Step 8a — NetScaler CPX/VPX configuration"
    ask "A10 host IP: "
    read -r A10_HOST
    [ -z "$A10_HOST" ] && error "A10 host IP is required"
    ask "NetScaler NITRO port [443]: "
    read -r A10_PORT; A10_PORT="${A10_PORT:-443}"
    ask "A10 username [admin]: "
    read -r A10_USER; A10_USER="${A10_USER:-admin}"
    ask "A10 password: "
    read -rs A10_PASS; echo
    [ -z "$A10_PASS" ] && error "A10 password is required"

    # Test connectivity
    ask "Test A10 connectivity now? [Y/n]: "
    read -r REPLY
    if [[ "${REPLY:-Y}" =~ ^[Yy]$ ]]; then
        echo "Testing AXAPI connection..."
        HTTP=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 \
            -X POST "https://$A10_HOST:$A10_PORT/axapi/v3/auth" \
            -H "Content-Type: application/json" \
            -d "{\"credentials\":{\"username\":\"$A10_USER\",\"password\":\"$A10_PASS\",\"method\":\"local\"}}")
        if [ "$HTTP" == "200" ]; then
            info "NetScaler NITRO reachable and credentials valid"
        else
            warn "NetScaler NITRO returned HTTP $HTTP — check host/credentials"
            ask "Continue anyway? [y/N]: "
            read -r REPLY
            [[ "${REPLY}" =~ ^[Yy]$ ]] || error "Fix A10 connectivity and re-run"
        fi
    fi

    mkdir -p backends
    cat > backends/a10.yml << EOF2
a10:
  type: a10
  host: $A10_HOST
  port: $A10_PORT
  username: $A10_USER
  password: "$A10_PASS"
  ssl: true
  verify_ssl: false
EOF2
    info "backends/a10.yml written"
fi


# ── Step 10: Write .env ───────────────────────────────────────────
step "Step 9 — Writing .env"
cat > .env << EOF2
TMOS_USERNAME=$NG1_USER
TMOS_PASSWORD=$NG1_PASS
TLS_CERT=certs/cert.pem
TLS_KEY=certs/key.pem
PORT=8443
TMOS_HOST_IP=$HOST_IP
BACKENDS_FILE=/app/backends.yml
EOF2
info ".env written"

# ── Step 11: TLS certificate ──────────────────────────────────────
step "Step 10 — TLS certificate"
if [ -f certs/cert.pem ] && [ -f certs/key.pem ]; then
    warn "Existing cert found — skipping generation"
else
    mkdir -p certs
    openssl req -x509 -newkey rsa:4096 -keyout certs/key.pem -out certs/cert.pem \
        -days 3650 -nodes -subj "/CN=ng1-f5-emulator" 2>/dev/null
    info "Self-signed TLS cert generated (10 year validity)"
fi

# ── Step 12: Build docker-compose ────────────────────────────────
step "Step 11 — Generating docker-compose.yml"
{
echo "services:"
if $ENABLE_A10; then
cat << 'EOF2'

  tmos-shim-a10:
    build: .
    container_name: tmos-shim-a10
    restart: unless-stopped
    ports:
      - "${TMOS_HOST_IP}:8446:8443"
    volumes:
      - ./certs:/app/certs:ro
      - ./backends/a10.yml:/app/backends.yml:ro
    env_file: [ .env ]
    extra_hosts:
      - "host.docker.internal:host-gateway"
    healthcheck:
      test: ["CMD","python","-c","import urllib.request,ssl; ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE; urllib.request.urlopen('https://localhost:8443/',context=ctx)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
EOF2
fi
} > docker-compose.yml
info "docker-compose.yml written"

# ── Step 13: Deploy ───────────────────────────────────────────────
step "Step 12 — Deploying"
docker compose down 2>/dev/null || true
docker compose build --no-cache
docker compose up -d

info "Waiting for containers to start..."
sleep 8

# ── Step 14: Health check ─────────────────────────────────────────
step "Step 13 — Health check"
FAIL=0
if $ENABLE_A10; then
    HTTP=$(curl -sk -u "$NG1_USER:$NG1_PASS" -o /dev/null -w "%{http_code}" --max-time 5 "https://$HOST_IP:8446/")
    [ "$HTTP" == "200" ] && info "A10 shim healthy → https://$HOST_IP:8446/" \
        || { warn "A10 shim HTTP $HTTP — check: docker logs tmos-shim-a10"; FAIL=1; }
fi

# ── Summary ───────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║                DEPLOYMENT SUMMARY                   ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  nG1 BIG-IP Discovery settings:"
echo -e "    Username       : ${BOLD}$NG1_USER${NC}"
echo -e "    Password       : ${BOLD}$NG1_PASS${NC}"
echo -e "    Login Provider : ${BOLD}tmos${NC}"
echo ""
$ENABLE_A10   && echo -e "  NetScaler adapter   →  Host: ${BOLD}$HOST_IP${NC}  Port: ${BOLD}8446${NC}"
echo ""
if [ $FAIL -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}✅ All services healthy — ready for nG1 registration${NC}"
else
    echo -e "  ${YELLOW}${BOLD}⚠  Some services need attention — check logs above${NC}"
fi
echo ""
echo -e "  Useful commands:"
echo -e "    docker compose ps"
echo -e "    docker logs tmos-shim-a10"
echo -e "    docker compose restart"
echo ""
