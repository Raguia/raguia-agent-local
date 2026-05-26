use rusqlite::{params, Connection};
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

        Ok(Self {
            conn: Arc::new(std::sync::Mutex::new(conn)),
        })
    }

    /// Enqueue (or re-enqueue) a file for sync.
    ///
    /// If the file already exists with the same rel_path, its status is reset
    /// to 'pending' and event_type updated. Matches Python behavior.
    pub fn enqueue(
        &self,
        rel_path: &str,
        abs_path: &str,
        event_type: &str,
    ) -> Result<(), QueueError> {
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
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE sync_queue SET status = 'synced', updated_at = datetime('now') WHERE rel_path = ?1",
            params![rel_path],
        )?;
        Ok(())
    }

    /// Mark an entry as failed with an error message and increment attempts.
    pub fn mark_error(&self, rel_path: &str, error: &str) -> Result<(), QueueError> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE sync_queue SET status = 'failed', attempts = attempts + 1,
             error = ?2, updated_at = datetime('now') WHERE rel_path = ?1",
            params![rel_path, error],
        )?;
        Ok(())
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
