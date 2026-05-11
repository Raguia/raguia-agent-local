"""File de synchronisation SQLite - resistante aux crashs et aux doublons."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
    rel_path  TEXT PRIMARY KEY,
    abs_path  TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'modified',
    queued_at REAL NOT NULL,
    attempts  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sync_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_path  TEXT NOT NULL,
    status    TEXT NOT NULL,
    synced_at REAL NOT NULL,
    error_msg TEXT
);
CREATE INDEX IF NOT EXISTS idx_queue_queued ON queue(queued_at);
CREATE INDEX IF NOT EXISTS idx_log_status  ON sync_log(status, synced_at);
"""

# Tentatives après lesquelles une ligne reste dans la queue mais est ignorée par pop_batch
# (sauf synchro forcée menu / portail — voir sync_agent.run_cycle).
MAX_TRIES_BEFORE_STUCK = 5


class QueueStore:
    """File SQLite thread-safe.

    Scenarios couverts :
    - Antivirus / doublons d'events  -> PRIMARY KEY deduplique
    - Agent crash entre deux syncs   -> fichiers restent dans queue (attempts++)
    - Dossier renomme/deplace        -> queue en dehors du dossier surveille
    - Word/Excel verrous             -> stability check dans pop_batch (min_age)
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # Init schema depuis le thread courant
        conn = self._conn()
        conn.executescript(_SCHEMA)
        conn.commit()
        self._ensure_event_type_column()

    # ------------------------------------------------------------------
    # Connexion par thread (SQLite n'est pas multi-thread safe en mode shared)
    # ------------------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=10.0,
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    def _ensure_event_type_column(self) -> None:
        """Migration legere : anciennes queue.db sans colonne event_type."""
        conn = self._conn()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(queue)")}
        if "event_type" not in cols:
            conn.execute(
                "ALTER TABLE queue ADD COLUMN event_type TEXT NOT NULL DEFAULT 'modified'"
            )
            conn.commit()

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------
    def enqueue(self, rel_path: str, abs_path: str, event_type: str = "modified") -> None:
        """Ajoute ou rafraichit un fichier dans la file (reset attempts)."""
        conn = self._conn()
        conn.execute(
            """
            INSERT INTO queue (rel_path, abs_path, event_type, queued_at, attempts)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(rel_path) DO UPDATE SET
                abs_path   = excluded.abs_path,
                event_type = excluded.event_type,
                queued_at  = excluded.queued_at,
                attempts   = 0
            """,
            (rel_path, abs_path, event_type, time.time()),
        )
        conn.commit()

    def pop_batch(
        self,
        max_n: int,
        min_age_seconds: float = 2.0,
        max_attempts: int = MAX_TRIES_BEFORE_STUCK,
    ) -> list[dict]:
        """Retourne un lot de fichiers stables (age >= min_age_seconds) et incremente attempts."""
        conn = self._conn()
        now = time.time()
        cutoff_normal = now - min_age_seconds
        cutoff_delete = now - 0.5
        rows = conn.execute(
            """
            SELECT rel_path, abs_path, event_type, queued_at, attempts
            FROM queue
            WHERE attempts < ? AND (
                (COALESCE(event_type, 'modified') = 'deleted' AND queued_at <= ?)
                OR (COALESCE(event_type, 'modified') <> 'deleted' AND queued_at <= ?)
            )
            ORDER BY
                CASE WHEN COALESCE(event_type, 'modified') = 'deleted' THEN 0 ELSE 1 END,
                queued_at
            LIMIT ?
            """,
            (max_attempts, cutoff_delete, cutoff_normal, max_n),
        ).fetchall()
        if not rows:
            return []
        conn.executemany(
            "UPDATE queue SET attempts = attempts + 1 WHERE rel_path = ?",
            [(r["rel_path"],) for r in rows],
        )
        conn.commit()
        return [dict(r) for r in rows]

    def mark_done(self, rel_path: str) -> None:
        conn = self._conn()
        conn.execute("DELETE FROM queue WHERE rel_path = ?", (rel_path,))
        conn.execute(
            "INSERT INTO sync_log (rel_path, status, synced_at) VALUES (?, 'ok', ?)",
            (rel_path, time.time()),
        )
        conn.commit()

    def mark_error(self, rel_path: str, error: str) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT INTO sync_log (rel_path, status, synced_at, error_msg) VALUES (?, 'error', ?, ?)",
            (rel_path, time.time(), error[:1000]),
        )
        conn.commit()

    def requeue(self, rel_path: str, abs_path: str) -> None:
        """Remet un fichier en file avec attempts=0 (ex: apres une erreur reseau)."""
        conn = self._conn()
        conn.execute(
            "UPDATE queue SET attempts=0, queued_at=? WHERE rel_path=?",
            (time.time(), rel_path),
        )
        conn.commit()

    def pending_count(self) -> int:
        return self._conn().execute("SELECT COUNT(*) FROM queue").fetchone()[0]

    def pending_delete_count(self) -> int:
        return self._conn().execute(
            "SELECT COUNT(*) FROM queue WHERE COALESCE(event_type, 'modified') = 'deleted'"
        ).fetchone()[0]

    def stuck_count(self, max_attempts: int = MAX_TRIES_BEFORE_STUCK) -> int:
        """Fichiers bloques (trop de tentatives) - souvent Word/Excel verrouillesou fichiers corrompus."""
        return self._conn().execute(
            "SELECT COUNT(*) FROM queue WHERE attempts >= ?", (max_attempts,)
        ).fetchone()[0]

    def reset_stuck(self, max_attempts: int = MAX_TRIES_BEFORE_STUCK) -> int:
        """Remet les fichiers bloques a zero (appele manuellement depuis le tray)."""
        conn = self._conn()
        cur = conn.execute(
            "UPDATE queue SET attempts=0, queued_at=? WHERE attempts >= ?",
            (time.time(), max_attempts),
        )
        conn.commit()
        return cur.rowcount

    def recent_errors(self, limit: int = 5) -> list[dict]:
        rows = self._conn().execute(
            """SELECT rel_path, synced_at, error_msg FROM sync_log
               WHERE status='error' ORDER BY synced_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def last_sync_at(self) -> float | None:
        row = self._conn().execute(
            "SELECT MAX(synced_at) FROM sync_log WHERE status='ok'"
        ).fetchone()
        return row[0] if row else None

    def wal_checkpoint(self, mode: str = "PASSIVE") -> tuple[int, int, int]:
        """Checkpoint WAL : libère l'espace disque sans blocage (PASSIVE).

        Retourne (busy, log, checkpointed) selon la doc SQLite.
        Appeler périodiquement (ex: toutes les 100 transactions).
        """
        conn = self._conn()
        row = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        return (row[0], row[1], row[2]) if row else (0, 0, 0)

    def close(self) -> None:
        if hasattr(self._local, "conn"):
            try:
                self._local.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            self._local.conn.close()
            del self._local.conn
