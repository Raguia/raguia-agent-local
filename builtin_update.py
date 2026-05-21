#!/usr/bin/env python3
"""Point d'entrée à la racine du clone — délègue au paquet installé.

    python3 builtin_update.py

Préférez aussi : ./update.sh ou ``python -m raguia_local_agent.local_git_update``
"""

from raguia_local_agent.local_git_update import main

if __name__ == "__main__":
    raise SystemExit(main())
