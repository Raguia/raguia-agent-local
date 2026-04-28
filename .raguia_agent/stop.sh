#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# Le lock d'instance unique est ecrit dans ~/.raguia/agent.pid (voir __main__.py).
PID_FILE="$HOME/.raguia/agent.pid"
if [ -f "$PID_FILE" ]; then
    pid=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE" 2>/dev/null || true
fi

# Fallback defensif : ne cible que les process de l'agent.
for pid in $(pgrep -f "raguia_local_agent" 2>/dev/null); do
    kill "$pid" 2>/dev/null || true
done

echo "Agent arrêté"
