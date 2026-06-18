use rusqlite::{params, Connection, OptionalExtension};
use std::path::Path;
use std::sync::Arc;
use thiserror::Error;

/// Queue store errors
#[derive(Error, Debug)]
pub enum QueueError {
    #[error("SQLite error: {0}")]
    Sqlite(#[from] rusqlite::Error),

    #[error("Config error: {0}")]
    Config(String),
}

/// Maximum attempts before a file is marked stuck
pub const MAX_TRIES_BEFORE_STUCK: u32 = 10;

/// A file pending sync in the queue
#[derive(Debug, Clone)]
pub struct SyncEntry {
    pub id: i64,
    /// Relative path from root (e.g. "Documents/report.pdf")
    pub rel_path: String,
    /// Absolute path for file access
    pub abs_path: String,
    /// Event type: "modified" or "deleted"
    pub event_type: String,
    /// Retry count
    pub attempts: u32,
    /// Last error message (if any)
    pub error: String,
}

/// Aggregate statistics for the sync queue
#[derive(Debug, Default)]
pub struct SyncStats {
    pub pending: u64,
    pub pending_delete: u64,
    pub synced: u64,
    pub stuck: u64,
}

/// SQLite-backed persistent queue for file sync operations.
///
/// Schema mirrors the Python ``queue_store.py``:
///   sync_queue(id INTEGER PK, rel_path TEXT UNIQUE, abs_path TEXT,
///              event_type TEXT, status TEXT, attempts INTEGER DEFAULT 0,
///              error TEXT, created_at TEXT, updated_at TEXT)
///
/// Thread-safe via internal ``Mutex<Connection>``.
pub struct Store {
    conn: Arc<std::sync::Mutex<Connection>>,
}

fn normalize_rel_path(rel_path: &str) -> String {
    rel_path
        .replace('\\', "/")
        .trim()
        .trim_matches('/')
        .to_string()
}

impl Store {
    /// Open (or create) the queue database at ``db_dir / sync_queue.sqlite``.
    pub fn new(db_dir: &Path) -> Result<Self, QueueError> {
        std::fs::create_dir_all(db_dir).ok();
        let db_path = db_dir.join("sync_queue.sqlite");
        let conn = Connection::open(&db_path)?;

        // Enable WAL mode for concurrent reads
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA busy_timeout=5000;

             CREATE TABLE IF NOT EXISTS sync_queue (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 rel_path TEXT NOT NULL UNIQUE,
                 abs_path TEXT NOT NULL DEFAULT '',
                 event_type TEXT NOT NULL DEFAULT 'modified',
                 status TEXT NOT NULL DEFAULT 'pending',
                 attempts INTEGER NOT NULL DEFAULT 0,
                 error TEXT NOT NULL DEFAULT '',
                 created_at TEXT NOT NULL DEFAULT (datetime('now')),
                 updated_at TEXT NOT NULL DEFAULT (datetime('now'))
             );

             CREATE INDEX IF NOT EXISTS idx_queue_status ON sync_queue(status);
             CREATE INDEX IF NOT EXISTS idx_queue_rel_path ON sync_queue(rel_path);",
        )?;

        if !column_exists(&conn, "sync_queue", "unstable_attempts")? {
            conn.execute(
                "ALTER TABLE sync_queue ADD COLUMN unstable_attempts INTEGER NOT NULL DEFAULT 0",
                [],
            )?;
        }
        normalize_existing_rel_paths(&conn)?;

        Ok(Self {
            conn: Arc::new(std::sync::Mutex::new(conn)),
        })
    }

    /// Enqueue (or re-enqueue) a file for sync.
    ///
    /// If the file already exists with the same rel_path, its status is reset
    /// to 'pending' and event_type updated. Matches Python behavior.
    /// Note: `unstable_attempts` is preserved (only cleared by `mark_done` or `reset_stuck`).
    pub fn enqueue(
        &self,
        rel_path: &str,
        abs_path: &str,
        event_type: &str,
    ) -> Result<(), QueueError> {
        let rel_path = normalize_rel_path(rel_path);
        if rel_path.is_empty() {
            return Ok(());
        }
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO sync_queue (rel_path, abs_path, event_type, status)
             VALUES (?1, ?2, ?3, 'pending')
             ON CONFLICT(rel_path) DO UPDATE SET
                abs_path = COALESCE(NULLIF(?2, ''), abs_path),
                event_type = ?3,
                status = 'pending',
                attempts = 0,
                error = '',
                updated_at = datetime('now')",
            params![rel_path, abs_path, event_type],
        )?;
        Ok(())
    }

    /// Increment the unstable-attempts counter for a re-enqueued file.
    /// Returns the new value so the caller can decide whether to give up.
    pub fn bump_unstable(&self, rel_path: &str) -> Result<u32, QueueError> {
        let rel_path = normalize_rel_path(rel_path);
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE sync_queue
             SET unstable_attempts = unstable_attempts + 1,
                 updated_at = datetime('now')
             WHERE rel_path = ?1",
            params![rel_path],
        )?;
        let n: i64 = conn.query_row(
            "SELECT unstable_attempts FROM sync_queue WHERE rel_path = ?1",
            params![rel_path],
            |row| row.get(0),
        )?;
        Ok(n as u32)
    }

    /// Threshold above which a perpetually-unstable file is marked failed
    /// (Bug E: prevents infinite re-enqueue loops for files still being written).
    pub const MAX_UNSTABLE_ATTEMPTS: u32 = 20;

    /// Pop a batch of pending entries for processing.
    ///
    /// Returns entries ordered by attempts ASC, then created_at ASC.
    /// Skips entries that exceed ``max_attempts``.
    /// Filters by ``min_age_seconds`` for stability checks.
    pub fn pop_batch(
        &self,
        max_count: u32,
        min_age_seconds: f64,
        max_attempts: u32,
    ) -> Result<Vec<SyncEntry>, QueueError> {
        let conn = self.conn.lock().unwrap();

        let age_filter = if min_age_seconds > 0.0 {
            format!(
                "AND datetime('now', '-{} seconds') >= updated_at",
                min_age_seconds as i64
            )
        } else {
            String::new()
        };

        let sql = format!(
            "SELECT id, rel_path, abs_path, event_type, attempts, error
             FROM sync_queue
             WHERE status = 'pending'
               AND attempts < ?1
               {}
             ORDER BY attempts ASC, created_at ASC
             LIMIT ?2",
            age_filter
        );

        let mut stmt = conn.prepare(&sql)?;
        let entries: Vec<SyncEntry> = stmt
            .query_map(params![max_attempts, max_count], |row| {
                Ok(SyncEntry {
                    id: row.get(0)?,
                    rel_path: row.get(1)?,
                    abs_path: row.get(2)?,
                    event_type: row.get(3)?,
                    attempts: row.get::<_, u32>(4)?,
                    error: row.get(5)?,
                })
            })?
            .filter_map(|r| r.ok())
            .collect();

        if entries.is_empty() {
            return Ok(vec![]);
        }

        // Mark entries as 'syncing' to avoid re-picking them
        for entry in &entries {
            conn.execute(
                "UPDATE sync_queue SET status = 'syncing', updated_at = datetime('now') WHERE id = ?1",
                params![entry.id],
            )?;
        }

        Ok(entries)
    }

    /// Mark an entry as successfully synced.
    pub fn mark_done(&self, rel_path: &str) -> Result<(), QueueError> {
        let rel_path = normalize_rel_path(rel_path);
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE sync_queue
             SET status = 'synced',
                 unstable_attempts = 0,
                 updated_at = datetime('now')
             WHERE rel_path = ?1",
            params![rel_path],
        )?;
        Ok(())
    }

    /// Mark an entry as failed with an error message and increment attempts.
    pub fn mark_error(&self, rel_path: &str, error: &str) -> Result<(), QueueError> {
        let rel_path = normalize_rel_path(rel_path);
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE sync_queue
             SET attempts = attempts + 1,
                 status = CASE WHEN attempts + 1 >= ?3 THEN 'failed' ELSE 'pending' END,
                 error = ?2,
                 updated_at = datetime('now')
             WHERE rel_path = ?1",
            params![rel_path, error, MAX_TRIES_BEFORE_STUCK],
        )?;
        Ok(())
    }

    /// Reset entries left in syncing state after a crash.
    pub fn reset_syncing(&self) -> Result<u64, QueueError> {
        let conn = self.conn.lock().unwrap();
        let count = conn.execute(
            "UPDATE sync_queue SET status = 'pending', error = '',
             updated_at = datetime('now')
             WHERE status = 'syncing'",
            [],
        )?;
        Ok(count as u64)
    }

    /// Reset all stuck or orphaned entries back to pending (attempts=0).
    /// Covers failed entries and entries left in 'syncing' state after a crash.
    pub fn reset_stuck(&self) -> Result<u64, QueueError> {
        let conn = self.conn.lock().unwrap();
        let count = conn.execute(
            "UPDATE sync_queue SET status = 'pending', attempts = 0, error = '',
             updated_at = datetime('now')
             WHERE status IN ('failed', 'syncing') OR attempts >= ?1",
            params![MAX_TRIES_BEFORE_STUCK],
        )?;
        Ok(count as u64)
    }

    /// Count entries that are stuck (exceeded max attempts)
    pub fn stuck_count(&self) -> Result<u64, QueueError> {
        let conn = self.conn.lock().unwrap();
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM sync_queue
             WHERE status IN ('failed', 'syncing') OR (status = 'pending' AND attempts >= ?1)",
            params![MAX_TRIES_BEFORE_STUCK],
            |row| row.get(0),
        )?;
        Ok(count as u64)
    }

    /// Count entries pending sync (status = 'pending' or 'syncing')
    pub fn pending_count(&self) -> Result<u64, QueueError> {
        let conn = self.conn.lock().unwrap();
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM sync_queue WHERE status IN ('pending', 'syncing')",
            [],
            |row| row.get(0),
        )?;
        Ok(count as u64)
    }

    /// Count pending delete entries
    pub fn pending_delete_count(&self) -> Result<u64, QueueError> {
        let conn = self.conn.lock().unwrap();
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM sync_queue
             WHERE status IN ('pending', 'syncing') AND event_type = 'deleted'",
            [],
            |row| row.get(0),
        )?;
        Ok(count as u64)
    }

    /// Get aggregate sync statistics
    pub fn get_stats(&self) -> Result<SyncStats, QueueError> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT
                COALESCE(SUM(CASE WHEN status IN ('pending', 'syncing') AND event_type != 'deleted' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN status IN ('pending', 'syncing') AND event_type = 'deleted' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN status = 'synced' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN status IN ('failed', 'syncing') OR attempts >= ?1 THEN 1 ELSE 0 END), 0)
             FROM sync_queue",
        )?;

        let stats = stmt.query_row(params![MAX_TRIES_BEFORE_STUCK], |row| {
            Ok(SyncStats {
                pending: row.get::<_, i64>(0)? as u64,
                pending_delete: row.get::<_, i64>(1)? as u64,
                synced: row.get::<_, i64>(2)? as u64,
                stuck: row.get::<_, i64>(3)? as u64,
            })
        })?;

        Ok(stats)
    }

    /// Run a WAL checkpoint (periodic maintenance)
    pub fn wal_checkpoint(&self, mode: &str) -> Result<(), QueueError> {
        let conn = self.conn.lock().unwrap();
        conn.execute_batch(&format!("PRAGMA wal_checkpoint({})", mode))?;
        Ok(())
    }

    /// Clean up old synced entries (keep last N days)
    pub fn clean_old_entries(&self, keep_days: u64) -> Result<u64, QueueError> {
        let conn = self.conn.lock().unwrap();
        let deleted = conn.execute(
            "DELETE FROM sync_queue WHERE status = 'synced' AND updated_at < datetime('now', ?1)",
            params![format!("-{} days", keep_days)],
        )?;
        Ok(deleted as u64)
    }
}

fn column_exists(conn: &Connection, table: &str, column: &str) -> Result<bool, rusqlite::Error> {
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({})", table))?;
    let mut rows = stmt.query([])?;
    while let Some(row) = rows.next()? {
        let name: String = row.get(1)?;
        if name == column {
            return Ok(true);
        }
    }
    Ok(false)
}

#[derive(Debug)]
struct QueueRow {
    id: i64,
    rel_path: String,
    abs_path: String,
    event_type: String,
    status: String,
    attempts: i64,
    error: String,
    unstable_attempts: i64,
}

fn read_queue_row(conn: &Connection, id: i64) -> Result<QueueRow, rusqlite::Error> {
    conn.query_row(
        "SELECT id, rel_path, abs_path, event_type, status, attempts, error, unstable_attempts
         FROM sync_queue WHERE id = ?1",
        params![id],
        |row| {
            Ok(QueueRow {
                id: row.get(0)?,
                rel_path: row.get(1)?,
                abs_path: row.get(2)?,
                event_type: row.get(3)?,
                status: row.get(4)?,
                attempts: row.get(5)?,
                error: row.get(6)?,
                unstable_attempts: row.get(7)?,
            })
        },
    )
}

fn merge_status(a: &str, b: &str) -> &'static str {
    if matches!(a, "pending" | "syncing") || matches!(b, "pending" | "syncing") {
        "pending"
    } else if a == "failed" || b == "failed" {
        "failed"
    } else {
        "synced"
    }
}

fn merge_duplicate_rel_path(
    conn: &Connection,
    existing_id: i64,
    incoming: &QueueRow,
    normalized: &str,
) -> Result<(), rusqlite::Error> {
    let existing = read_queue_row(conn, existing_id)?;
    let abs_path = if incoming.abs_path.is_empty() {
        existing.abs_path
    } else {
        incoming.abs_path.clone()
    };
    let event_type = if incoming.event_type == "deleted" || existing.event_type != "deleted" {
        incoming.event_type.clone()
    } else {
        existing.event_type
    };
    let status = merge_status(&existing.status, &incoming.status);
    let attempts = existing.attempts.max(incoming.attempts);
    let error = if incoming.error.is_empty() {
        existing.error
    } else {
        incoming.error.clone()
    };
    let unstable_attempts = existing.unstable_attempts.max(incoming.unstable_attempts);

    conn.execute(
        "UPDATE sync_queue
         SET rel_path = ?2,
             abs_path = ?3,
             event_type = ?4,
             status = ?5,
             attempts = ?6,
             error = ?7,
             unstable_attempts = ?8,
             updated_at = datetime('now')
         WHERE id = ?1",
        params![
            existing_id,
            normalized,
            abs_path,
            event_type,
            status,
            attempts,
            error,
            unstable_attempts,
        ],
    )?;
    conn.execute("DELETE FROM sync_queue WHERE id = ?1", params![incoming.id])?;
    Ok(())
}

fn normalize_existing_rel_paths(conn: &Connection) -> Result<(), rusqlite::Error> {
    let mut stmt = conn.prepare("SELECT id FROM sync_queue ORDER BY id")?;
    let ids: Vec<i64> = stmt
        .query_map([], |row| row.get(0))?
        .collect::<Result<_, _>>()?;

    for id in ids {
        let row = match read_queue_row(conn, id).optional()? {
            Some(row) => row,
            None => continue,
        };
        let normalized = normalize_rel_path(&row.rel_path);
        if normalized.is_empty() {
            conn.execute("DELETE FROM sync_queue WHERE id = ?1", params![id])?;
            continue;
        }
        if normalized == row.rel_path {
            continue;
        }

        let existing_id: Option<i64> = conn
            .query_row(
                "SELECT id FROM sync_queue WHERE rel_path = ?1 AND id != ?2 LIMIT 1",
                params![normalized, id],
                |r| r.get(0),
            )
            .optional()?;

        if let Some(existing_id) = existing_id {
            merge_duplicate_rel_path(conn, existing_id, &row, &normalized)?;
        } else {
            conn.execute(
                "UPDATE sync_queue SET rel_path = ?2, updated_at = datetime('now') WHERE id = ?1",
                params![id, normalized],
            )?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_db_dir(name: &str) -> std::path::PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "raguia-agent-{}-{}-{}",
            name,
            std::process::id(),
            nanos
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn store_can_open_existing_db_after_migration() {
        let dir = temp_db_dir("migration");
        let first = Store::new(&dir).unwrap();
        first.enqueue("a.pdf", "/tmp/a.pdf", "modified").unwrap();
        drop(first);

        let second = Store::new(&dir).unwrap();
        assert_eq!(second.pending_count().unwrap(), 1);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn enqueue_normalizes_windows_relative_paths() {
        let dir = temp_db_dir("relpath");
        let store = Store::new(&dir).unwrap();
        store
            .enqueue(r"Factures\2026\a.pdf", "/tmp/a.pdf", "modified")
            .unwrap();
        store
            .enqueue("Factures/2026/a.pdf", "/tmp/a2.pdf", "modified")
            .unwrap();

        let batch = store.pop_batch(10, 0.0, MAX_TRIES_BEFORE_STUCK).unwrap();
        assert_eq!(batch.len(), 1);
        assert_eq!(batch[0].rel_path, "Factures/2026/a.pdf");
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn migration_merges_existing_windows_path_duplicates() {
        let dir = temp_db_dir("relpath-migration");
        let db_path = dir.join("sync_queue.sqlite");
        {
            let conn = Connection::open(&db_path).unwrap();
            conn.execute_batch(
                "CREATE TABLE sync_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rel_path TEXT NOT NULL UNIQUE,
                    abs_path TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL DEFAULT 'modified',
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );",
            )
            .unwrap();
            conn.execute(
                "INSERT INTO sync_queue (rel_path, abs_path, event_type, status, attempts)
                 VALUES (?1, ?2, 'modified', 'synced', 0)",
                params!["Factures/2026/a.pdf", "/tmp/old.pdf"],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO sync_queue (rel_path, abs_path, event_type, status, attempts)
                 VALUES (?1, ?2, 'deleted', 'pending', 3)",
                params![r"Factures\2026\a.pdf", "/tmp/new.pdf"],
            )
            .unwrap();
        }

        let store = Store::new(&dir).unwrap();
        let batch = store.pop_batch(10, 0.0, MAX_TRIES_BEFORE_STUCK).unwrap();

        assert_eq!(batch.len(), 1);
        assert_eq!(batch[0].rel_path, "Factures/2026/a.pdf");
        assert_eq!(batch[0].abs_path, "/tmp/new.pdf");
        assert_eq!(batch[0].event_type, "deleted");
        assert_eq!(batch[0].attempts, 3);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn mark_error_retries_before_stuck() {
        let dir = temp_db_dir("retries");
        let store = Store::new(&dir).unwrap();
        store.enqueue("a.pdf", "/tmp/a.pdf", "modified").unwrap();

        for _ in 0..MAX_TRIES_BEFORE_STUCK - 1 {
            store.mark_error("a.pdf", "network").unwrap();
            assert_eq!(store.pending_count().unwrap(), 1);
            assert_eq!(store.stuck_count().unwrap(), 0);
        }

        store.mark_error("a.pdf", "network").unwrap();
        assert_eq!(store.pending_count().unwrap(), 0);
        assert_eq!(store.stuck_count().unwrap(), 1);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn reset_syncing_recovers_crash_left_entries() {
        let dir = temp_db_dir("syncing");
        let store = Store::new(&dir).unwrap();
        store.enqueue("a.pdf", "/tmp/a.pdf", "modified").unwrap();
        assert_eq!(
            store
                .pop_batch(1, 0.0, MAX_TRIES_BEFORE_STUCK)
                .unwrap()
                .len(),
            1
        );

        assert_eq!(store.reset_syncing().unwrap(), 1);
        assert_eq!(store.pending_count().unwrap(), 1);
        assert_eq!(store.stuck_count().unwrap(), 0);
        let _ = std::fs::remove_dir_all(dir);
    }
}
