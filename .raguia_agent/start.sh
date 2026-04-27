#!/bin/bash
cd "$(dirname "$0")"
# Racine du clone git (parent de .raguia_agent) — utilisé par builtin_update.py / MAJ depuis le menu
export RAGUIA_AGENT_REPO="$(cd .. && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Erreur: python3 est introuvable. Installe Python 3 puis relance."
  exit 1
fi

if [ ! -f venv/bin/activate ]; then
  python3 -m venv venv || exit 1
fi

source venv/bin/activate
export RAGUIA_AGENT_CONFIG="$(pwd)/raguia_agent.yaml"

# Garantit la presence des deps tray (pystray/Pillow) pour l'icone macOS.
if ! python3 -c "import pystray, PIL" >/dev/null 2>&1; then
  python3 -m pip install -e "..[tray]" || exit 1
fi

python3 -m raguia_local_agent
