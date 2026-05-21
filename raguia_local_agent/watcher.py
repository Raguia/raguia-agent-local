"""Surveillance recursive du dossier RAGUIA (watchdog).

Filtrage des fichiers temporaires :
- ~$*.docx/.xlsx  (Word/Excel en cours d'edition)
- *.tmp, *.part, *.crdownload, *.download
- .* (fichiers caches systeme)
- .raguia_state* (etat interne de l'agent)

La deduplication des evenements est geree par QueueStore (PRIMARY KEY SQLite).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger(__name__)

# Extensions et prefixes de fichiers temporaires a ignorer
_IGNORE_PREFIXES = ("~$", ".~", "._")
_IGNORE_EXTENSIONS = {
    ".tmp",
    ".part",
    ".crdownload",
    ".download",
    ".swp",
    ".swo",
    ".bak",  # editeurs Linux/vim
    ".orig",  # git/patch
}
_IGNORE_NAMES = {
    ".raguia_state.json",
    ".raguia_state.json.tmp",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
}


def _should_ignore(path: Path) -> bool:
    """Retourne True si le fichier doit etre ignore."""
    # Securite : ignorer les liens symboliques (evite d'uploader /etc/passwd si l'utilisateur cree un lien)
    if path.is_symlink():
        return True

    name = path.name
    # Fichiers systeme et etat interne
    if name in _IGNORE_NAMES:
        return True
    # Fichiers caches (commencent par .)
    if name.startswith("."):
        return True
    # Prefixes temporaires Word/Excel/LibreOffice
    if any(name.startswith(p) for p in _IGNORE_PREFIXES):
        return True
    # Extensions temporaires
    if path.suffix.lower() in _IGNORE_EXTENSIONS:
        return True
    return False


class _Handler(FileSystemEventHandler):
    def __init__(
        self,
        root: Path,
        on_event: Callable[[Path, str], None],
    ) -> None:
        super().__init__()
        self.root = root
        self.on_event = on_event

    def _dispatch_path(self, src: str, kind: str) -> None:
        p = Path(src)
        if _should_ignore(p):
            return
        self.on_event(p, kind)

    def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._dispatch_path(event.src_path, "created")

    def on_modified(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._dispatch_path(event.src_path, "modified")

    def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.is_directory:
            return
        src = Path(event.src_path)
        dest = Path(event.dest_path)
        try:
            src.relative_to(self.root)
            src_in = True
        except ValueError:
            src_in = False
        if not src_in:
            return
        try:
            dest.relative_to(self.root)
            dest_in = True
        except ValueError:
            dest_in = False
        if not dest_in:
            # Corbeille, autre volume, etc. : le fichier n'est plus sous RAGUIA
            self._dispatch_path(str(src), "deleted")
            return
        self._dispatch_path(event.dest_path, "moved")

    def on_deleted(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._dispatch_path(event.src_path, "deleted")


def start_observer(
    root: Path,
    on_event: Callable[[Path, str], None],
) -> tuple[Observer, threading.Thread]:
    """Demarre la surveillance ; retourne (observer, thread_courant)."""
    root.mkdir(parents=True, exist_ok=True)
    handler = _Handler(root, on_event)
    observer = Observer()
    observer.schedule(handler, str(root), recursive=True)
    observer.start()
    log.info("Observer watchdog demarre sur %s", root)
    return observer, threading.current_thread()


def stable_file(path: Path, stability_seconds: float) -> bool:
    """True si le fichier n'a pas ete modifie depuis au moins stability_seconds.

    Utilise la mtime vs temps courant (pas de sleep bloquant).
    """
    try:
        st = path.stat()
    except OSError:
        return False
    age = time.time() - st.st_mtime
    return age >= stability_seconds and st.st_size > 0
