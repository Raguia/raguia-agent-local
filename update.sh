#!/usr/bin/env bash
# MAJ complète depuis le même dépôt git que git clone — comme une réinstallation sans refaire tout le questionnaire.
#
# À lancer depuis la RACINE du clone (même dossier que install.sh) :
#   ./update.sh
#
# Variables optionnelles :
#   RAGUIA_AGENT_BRANCH   branche git (defaut : main)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}=== Mise à jour Agent Raguia (git pull + dépendances) ===${NC}"

if [[ ! -d .git ]]; then
    echo -e "${YELLOW}Pas de dossier .git dans ${SCRIPT_DIR}.${NC}"
    echo "Sans dépôt git, refaites : git clone https://github.com/ValMtp3/raguia-agent-local.git puis install.sh"
    exit 1
fi

BRANCH="${RAGUIA_AGENT_BRANCH:-main}"
echo -e "${GREEN}git fetch / pull (${BRANCH})…${NC}"
git fetch origin "${BRANCH}" || true
if ! git pull --ff-only "origin" "${BRANCH}" 2>/dev/null; then
    echo "Essai sans --ff-only (conflits possibles à résoudre à la main)…"
    git pull "origin" "${BRANCH}"
fi

AGENT_DIR="${SCRIPT_DIR}/.raguia_agent"
if [[ ! -d "${AGENT_DIR}/venv" ]]; then
    echo "venv introuvable dans ${AGENT_DIR}/venv — lancez une fois ./install.sh"
    exit 1
fi

echo -e "${GREEN}Réinstallation du paquet editable (venv .raguia_agent)…${NC}"
# shellcheck source=/dev/null
source "${AGENT_DIR}/venv/bin/activate"

if command -v uv >/dev/null 2>&1; then
    uv pip install -e ".[tray]"
else
    python3 -m pip install -e ".[tray]"
fi

echo ""
echo -e "${GREEN}=== Terminé.${NC}"
echo "Redémarrez l’agent : clic droit sur l’icône → Quitter, puis ouvrir à nouveau ${AGENT_DIR}/start.sh (ou votre raccourci)."
