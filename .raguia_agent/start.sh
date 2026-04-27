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

# Interprete du venv : uv peut ne creer que ``python``, pas ``python3``.
VENV_PY=""
for cand in venv/bin/python venv/bin/python3; do
  if [ -x "$cand" ]; then
    VENV_PY="$cand"
    break
  fi
done
if [ -z "$VENV_PY" ]; then
  echo "Erreur: aucun interprete executable dans venv/bin (python / python3)."
  exit 1
fi

# Garantit la presence des deps tray (pystray/Pillow) pour l'icone macOS.
if ! "$VENV_PY" -c "import pystray, PIL" >/dev/null 2>&1; then
  "$VENV_PY" -m pip install -e "..[tray]" || exit 1
fi

exec "$VENV_PY" -m raguia_local_agent
