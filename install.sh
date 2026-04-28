#!/bin/bash
# Installation simplifiée de l'agent RAGUIA (macOS / Linux ; détection auto de l'OS)
#
# Modes d'invocation :
#   1) Nouveau (recommandé) — seul le slug client change entre dev et prod :
#        ./install.sh prod  <client-slug>  [TOKEN]  [WATCH_PARENT]
#        ./install.sh local <client-slug>  [TOKEN]  [WATCH_PARENT]
#      Sans argument : pose les questions (mode, slug, jeton, dossier).
#
#   2) Ancien (compatibilité) — URL API complète en premier argument :
#        ./install.sh https://...  [TOKEN]  [WATCH_PARENT]  [prod|local]
#
# Environnement (optionnel) :
#   RAGUIA_INSTALL_ENV=prod|local     — défaut pour les invites interactives
#   RAGUIA_PORTAL_ORIGIN_PROD=...    — origine prod (api_base prod)
#   RAGUIA_LOCAL_API_BASE=...        — origine dev (défaut http://localhost:5173, proxy Vite → :8000)
#                                      Mettre http://127.0.0.1:8000 pour parler au backend seul.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${YELLOW}=== Installation Agent RAGUIA ===${NC}"

DEFAULT_API_PROD="${RAGUIA_PORTAL_ORIGIN_PROD:-https://raguia.valentin-fiess.fr}"
DEFAULT_API_LOCAL="${RAGUIA_LOCAL_API_BASE:-http://localhost:5173}"

LEGACY_MODE=0
if [[ -n "${1:-}" && "$1" =~ ^https?:// ]]; then
    LEGACY_MODE=1
fi

API_BASE=""
TOKEN=""
WATCH_PARENT=""
RUNTIME_ENV=""
CLIENT_SLUG=""

if [[ "$LEGACY_MODE" -eq 1 ]]; then
    API_BASE="${1:-}"
    TOKEN="${2:-}"
    WATCH_PARENT="${3:-}"
    RUNTIME_ENV="${4:-prod}"
    if [[ "$RUNTIME_ENV" != "local" && "$RUNTIME_ENV" != "prod" ]]; then
        RUNTIME_ENV="prod"
    fi
elif [[ -n "${1:-}" && ( "$1" == "prod" || "$1" == "local" ) ]]; then
    RUNTIME_ENV="$1"
    CLIENT_SLUG="${2:-}"
    TOKEN="${3:-}"
    WATCH_PARENT="${4:-}"
elif [[ -n "${1:-}" ]]; then
    echo -e "${RED}Premier argument invalide.${NC}"
    echo "  Nouveau : $0 prod|local <slug-client> [TOKEN] [WATCH_PARENT]"
    echo "  Ancien  : $0 https://origin-api [TOKEN] [WATCH_PARENT] [prod|local]"
    exit 1
else
    # Interactif (nouveau flux)
    DEF_MODE="${RAGUIA_INSTALL_ENV:-prod}"
    read -r -p "Mode [prod / local] (defaut: $DEF_MODE): " _mode
    RUNTIME_ENV="${_mode:-$DEF_MODE}"
    if [[ "$RUNTIME_ENV" != "local" ]]; then
        RUNTIME_ENV="prod"
    fi
    read -r -p "Slug portail / identifiant client (ex: client-acme): " CLIENT_SLUG
fi

if [[ "$RUNTIME_ENV" == "local" ]]; then
    DEFAULT_API_BASE="$DEFAULT_API_LOCAL"
else
    RUNTIME_ENV="prod"
    DEFAULT_API_BASE="$DEFAULT_API_PROD"
fi

if [[ "$LEGACY_MODE" -eq 0 ]]; then
    if [[ -z "${CLIENT_SLUG// /}" ]]; then
        read -r -p "Slug portail / identifiant client (ex: client-acme): " CLIENT_SLUG
    fi
    if [[ -z "${CLIENT_SLUG// /}" ]]; then
        echo -e "${RED}Le slug client est obligatoire (portion /portal/<slug>).${NC}"
        exit 1
    fi
    API_BASE="$DEFAULT_API_BASE"
fi

if [[ -z "$API_BASE" ]]; then
    read -r -p "URL API — api_base (defaut: $DEFAULT_API_BASE): " API_BASE
    API_BASE="${API_BASE:-$DEFAULT_API_BASE}"
fi
if [[ -z "$TOKEN" ]]; then
    read -r -s -p "Jeton JWT agent: " TOKEN
    echo ""
fi
if [[ -z "$API_BASE" || -z "$TOKEN" ]]; then
    echo -e "${RED}api_base et jeton sont obligatoires.${NC}"
    exit 1
fi

if [[ -z "$WATCH_PARENT" ]]; then
    read -r -p "Dossier parent (defaut: $HOME/Documents): " WATCH_PARENT
    WATCH_PARENT="${WATCH_PARENT:-$HOME/Documents}"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$SCRIPT_DIR/.raguia_agent"
PLIST_LABEL="com.raguia.local.agent"
SYSTEMD_USER_UNIT="raguia-agent.service"

install_git_if_missing() {
    if command -v git >/dev/null 2>&1; then
        return 0
    fi
    echo -e "${YELLOW}git absent: tentative d'installation automatique...${NC}"

    case "$(uname -s)" in
        Darwin)
            if command -v brew >/dev/null 2>&1; then
                brew install git || true
            else
                echo -e "${YELLOW}Homebrew absent. Installation de git non automatique sur ce mac.${NC}"
            fi
            ;;
        Linux)
            if command -v apt-get >/dev/null 2>&1; then
                if command -v sudo >/dev/null 2>&1; then
                    sudo apt-get update && sudo apt-get install -y git || true
                else
                    apt-get update && apt-get install -y git || true
                fi
            elif command -v dnf >/dev/null 2>&1; then
                if command -v sudo >/dev/null 2>&1; then
                    sudo dnf install -y git || true
                else
                    dnf install -y git || true
                fi
            elif command -v yum >/dev/null 2>&1; then
                if command -v sudo >/dev/null 2>&1; then
                    sudo yum install -y git || true
                else
                    yum install -y git || true
                fi
            elif command -v pacman >/dev/null 2>&1; then
                if command -v sudo >/dev/null 2>&1; then
                    sudo pacman -Sy --noconfirm git || true
                else
                    pacman -Sy --noconfirm git || true
                fi
            elif command -v zypper >/dev/null 2>&1; then
                if command -v sudo >/dev/null 2>&1; then
                    sudo zypper --non-interactive install git || true
                else
                    zypper --non-interactive install git || true
                fi
            else
                echo -e "${YELLOW}Aucun gestionnaire supporte detecte (apt/dnf/yum/pacman/zypper).${NC}"
            fi
            ;;
        *)
            echo -e "${YELLOW}OS non reconnu pour l'installation automatique de git.${NC}"
            ;;
    esac

    if ! command -v git >/dev/null 2>&1; then
        echo -e "${RED}git reste introuvable.${NC}"
        echo "Installez git puis relancez: https://git-scm.com/downloads"
        exit 1
    fi
}

PORTAL_HINT=""
if [[ -n "$CLIENT_SLUG" ]]; then
    PORTAL_HINT="$API_BASE/portal/$CLIENT_SLUG"
fi

install_autostart_macos() {
    local plist="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${PLIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${AGENT_DIR}/start.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${AGENT_DIR}</string>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
EOF
    chmod 644 "$plist"
    # Décharger l'ancienne instance si elle existe
    launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || launchctl unload "$plist" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null || launchctl load "$plist" 2>/dev/null || true
    echo -e "  ${GREEN}Démarrage automatique : LaunchAgent installé (~/.raguia_agent/start.sh).${NC}"
    echo "    Fichier : $plist"
}

install_autostart_linux() {
    if ! command -v systemctl &>/dev/null; then
        echo -e "  ${YELLOW}systemd absent : démarrage automatique non configuré (lancez .raguia_agent/start.sh manuellement ou via cron).${NC}"
        return 0
    fi
    local userdir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$userdir"
    local unit="$userdir/${SYSTEMD_USER_UNIT}"
    cat > "$unit" << EOF
[Unit]
Description=Raguia agent local
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${AGENT_DIR}
ExecStart=/bin/bash ${AGENT_DIR}/start.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now "${SYSTEMD_USER_UNIT}" 2>/dev/null || {
        echo -e "  ${YELLOW}Impossible d'activer le service utilisateur systemd.${NC}"
        echo "    Essayez : loginctl enable-linger \$USER  puis relancez, ou démarrez avec :"
        echo "      systemctl --user start ${SYSTEMD_USER_UNIT}"
        return 0
    }
    echo -e "  ${GREEN}Démarrage automatique : service utilisateur systemd activé.${NC}"
    echo "    Unit : $unit"
}

echo -e "\n${GREEN}1. Installation de 'uv' et Python...${NC}"
install_git_if_missing
if ! command -v uv &> /dev/null; then
    if ! command -v curl >/dev/null 2>&1; then
        echo -e "${RED}curl est requis pour installer uv automatiquement.${NC}"
        exit 1
    fi
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
    echo -e "${RED}uv est introuvable après installation.${NC}"
    exit 1
fi
uv python install 3.11

echo -e "\n${GREEN}2. Création de la configuration...${NC}"
mkdir -p "$AGENT_DIR"

{
    echo "api_base: \"$API_BASE\""
    if [[ -n "$CLIENT_SLUG" ]]; then
        echo "client_slug: \"$CLIENT_SLUG\""
    fi
    echo "agent_token: \"$TOKEN\""
    echo "watch_parent: \"$WATCH_PARENT\""
    echo "root_folder_name: \"RAGUIA\""
    echo "runtime_env: \"$RUNTIME_ENV\""
} > "$AGENT_DIR/raguia_agent.yaml"

echo -e "\n${GREEN}3. Installation des dépendances...${NC}"
cd "$SCRIPT_DIR"
uv venv "$AGENT_DIR/venv" --python 3.11
source "$AGENT_DIR/venv/bin/activate"
uv pip install -e ".[tray]"

echo -e "\n${GREEN}4. Test de connexion...${NC}"
if python -c "
import httpx, yaml, sys
with open('$AGENT_DIR/raguia_agent.yaml') as f: cfg = yaml.safe_load(f)
r = httpx.get(cfg['api_base'] + '/api/portal/agent/sync-status', headers={'Authorization': f'Bearer {cfg[\"agent_token\"]}'}, timeout=10.0)
if r.status_code != 200:
    sys.exit(1)
data = r.json()
sys.exit(0 if isinstance(data, dict) else 1)
" 2>/dev/null; then
    echo "  Connexion réussie!"
else
    echo -e "${RED}  Échec de connexion au portail.${NC}"
fi

echo -e "\n${GREEN}5. Scripts de contrôle...${NC}"
chmod +x "$SCRIPT_DIR/update.sh" 2>/dev/null || true
chmod +x "$AGENT_DIR/start.sh" "$AGENT_DIR/test.sh" "$AGENT_DIR/stop.sh" 2>/dev/null || true

echo -e "\n${GREEN}6. Démarrage automatique (selon l'OS)...${NC}"
case "$(uname -s)" in
    Darwin)   install_autostart_macos ;;
    Linux)    install_autostart_linux ;;
    *)        echo -e "  ${YELLOW}OS non pris en charge pour l'auto-config : exécutez manuellement ${AGENT_DIR}/start.sh${NC}" ;;
esac

mkdir -p "$WATCH_PARENT/RAGUIA"
echo -e "\n${GREEN}=== Installation terminée! ===${NC}"
echo -e "api_base (API agent) : ${CYAN}$API_BASE${NC}"
if [[ -n "$PORTAL_HINT" ]]; then
    echo -e "Portail (navigateur) : ${CYAN}$PORTAL_HINT${NC}"
fi
echo "Dossier synchronisé : $WATCH_PARENT/RAGUIA"
echo "Contrôle : ${AGENT_DIR}/test.sh | start.sh | stop.sh"
echo "MAJ depuis Git (racine du clone) : ${SCRIPT_DIR}/update.sh"
