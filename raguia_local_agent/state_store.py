"""Registre inode/chemin (JSON atomique) — suivi renommage/deplacement.

La file d'attente (pending) est desormais dans QueueStore (SQLite).
Ce module garde uniquement le registre des fichiers deja connus
pour detecter les renommages.
"""

from __future__ import annotations

import json
import os
import platform
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class FileRecord:
    rel_path: str
    external_id: str
    size: int
    mtime: float
    needs_review: bool = False


@dataclass
class AgentState:
    version: int = 2
    files: dict[str, FileRecord] = field(default_factory=dict)
    by_external: dict[str, str] = field(default_factory=dict)
    last_sync_ts: float = 0.0  # garde pour compat lecture v1

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "files": {k: asdict(v) for k, v in self.files.items()},
            "by_external": dict(self.by_external),
            "last_sync_ts": self.last_sync_ts,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "AgentState":
        st = cls()
        st.version = int(data.get("version", 1))
        st.last_sync_ts = float(data.get("last_sync_ts", 0.0))
        st.by_external = dict(data.get("by_external", {}))
        for rel, row in data.get("files", {}).items():
            st.files[rel] = FileRecord(
                rel_path=row["rel_path"],
                external_id=row["external_id"],
                size=int(row["size"]),
                mtime=float(row["mtime"]),
                needs_review=bool(row.get("needs_review", False)),
            )
        return st


def _fallback_external_id(path: Path, size: int, mtime: float) -> str:
    """Identifiant stable et deterministe (SHA256) si inode/dev peu fiables.

    Utilise hashlib.sha256 et non hash() car hash() est non-deterministe
    entre les lancements Python (PYTHONHASHSEED aleatoire).
    """
    import hashlib

    key = f"{path.resolve()}:{size}:{mtime}".encode()
    return f"fb:{hashlib.sha256(key).hexdigest()[:32]}"


def _external_id_for_path(path: Path) -> str:
    try:
        st = os.stat(path)
        dev, ino = int(st.st_dev), int(st.st_ino)
        if ino == 0 and platform.system() == "Windows":
            return _fallback_external_id(path, st.st_size, st.st_mtime)
        return f"native:{dev}:{ino}"
    except OSError:
        return f"missing:{path}"


class StateStore:
    """Registre JSON thread-safe (inode tracking + detection renommage)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.state = AgentState()
        if path.is_file():
            try:
                with open(path, encoding="utf-8") as f:
                    self.state = AgentState.from_json(json.load(f))
            except (json.JSONDecodeError, OSError, KeyError):
                self.state = AgentState()

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state.to_json(), f, indent=2, ensure_ascii=False)
            tmp.replace(self.path)

    def register_or_replace(
        self, root: Path, abs_path: Path
    ) -> tuple[str, Optional[str], bool, str]:
        """Enregistre ou met a jour un fichier.

        Retourne (rel_posix, ancien_rel_si_renomme, needs_review, external_id).
        """
        try:
            rel = abs_path.relative_to(root).as_posix()
        except ValueError:
            return "", None, True, ""

        ext = _external_id_for_path(abs_path)
        try:
            st = os.stat(abs_path)
            size, mtime = int(st.st_size), float(st.st_mtime)
        except OSError:
            return rel, None, True, ext

        with self._lock:
            old_rel = self.state.by_external.get(ext)
            if old_rel and old_rel != rel:
                # Renommage/deplacement detecte
                if old_rel in self.state.files:
                    del self.state.files[old_rel]
                self.state.files[rel] = FileRecord(rel, ext, size, mtime)
                self.state.by_external[ext] = rel
                return rel, old_rel, False, ext

            if rel in self.state.files:
                rec = self.state.files[rel]
                if rec.external_id != ext:
                    ambiguous = (
                        ext in self.state.by_external
                        and self.state.by_external[ext] != rel
                    )
                    if ambiguous:
                        self.state.files[rel] = FileRecord(
                            rel, ext, size, mtime, needs_review=True
                        )
                        return rel, None, True, ext
                    self.state.by_external.pop(rec.external_id, None)

            self.state.files[rel] = FileRecord(rel, ext, size, mtime)
            self.state.by_external[ext] = rel
            return rel, None, False, ext

    def remove_path(self, root: Path, abs_path: Path) -> None:
        try:
            rel = abs_path.relative_to(root).as_posix()
        except ValueError:
            return
        self.remove_rel(rel)

    def remove_rel(self, rel: str) -> None:
        """Retire un chemin relatif du registre (fichier supprimé ou poubelle distante)."""
        rel = rel.replace("\\", "/").strip().strip("/")
        if not rel:
            return
        with self._lock:
            if rel not in self.state.files:
                return
            ext = self.state.files[rel].external_id
            del self.state.files[rel]
            if self.state.by_external.get(ext) == rel:
                del self.state.by_external[ext]
