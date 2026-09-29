use rusqlite::{params, Connection, OpenFlags};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use thiserror::Error;

use crate::crypto::now_timestamp;
use crate::models::CivEvent;

#[derive(Error, Debug)]
pub enum EventStoreError {
    #[error("SQLite database error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("JSON serialization error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("Poison lock error")]
    LockPoisoned,
}

#[derive(Clone)]
pub struct CivEventStore {
    conn: Arc<Mutex<Connection>>,
    db_path: Option<PathBuf>,
}

impl CivEventStore {
    /// In-memory event store (ideal for tests and ephemeral runs)
    pub fn in_memory() -> Result<Self, EventStoreError> {
        let conn = Connection::open_in_memory()?;
        let store = Self {
            conn: Arc::new(Mutex::new(conn)),
            db_path: None,
        };
        store.init_schema()?;
        Ok(store)
    }

    /// File-backed persistent event store with WAL mode
    pub fn open(path: impl AsRef<Path>) -> Result<Self, EventStoreError> {
        let path_buf = path.as_ref().to_path_buf();
        if let Some(parent) = path_buf.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let conn = Connection::open_with_flags(
            &path_buf,
            OpenFlags::SQLITE_OPEN_READ_WRITE
                | OpenFlags::SQLITE_OPEN_CREATE
                | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;

        // Configure WAL and durability settings matching Hermes platform standards
        conn.execute_batch(
            "PRAGMA journal_mode = WAL;
             PRAGMA synchronous = NORMAL;
             PRAGMA foreign_keys = ON;
             PRAGMA busy_timeout = 5000;",
        )?;

        let store = Self {
            conn: Arc::new(Mutex::new(conn)),
            db_path: Some(path_buf),
        };
        store.init_schema()?;
        Ok(store)
    }

    pub fn db_path(&self) -> Option<&PathBuf> {
        self.db_path.as_ref()
    }

    fn init_schema(&self) -> Result<(), EventStoreError> {
        let conn = self.conn.lock().map_err(|_| EventStoreError::LockPoisoned)?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                seq INTEGER UNIQUE,
                name TEXT NOT NULL,
                trace_id TEXT,
                correlation_id TEXT,
                causation_id TEXT,
                trust_level TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                timestamp REAL NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_civ_events_name ON events(name);
            CREATE INDEX IF NOT EXISTS idx_civ_events_seq ON events(seq);
            CREATE INDEX IF NOT EXISTS idx_civ_events_correlation ON events(correlation_id);",
        )?;
        Ok(())
    }

    pub fn append(&self, event: &CivEvent) -> Result<i64, EventStoreError> {
        let conn = self.conn.lock().map_err(|_| EventStoreError::LockPoisoned)?;
        let tx = conn.unchecked_transaction()?;

        // Idempotency check: if event_id already exists, return existing seq
        let existing_seq: Option<i64> = tx
            .query_row(
                "SELECT seq FROM events WHERE event_id = ?1",
                params![event.event_id],
                |row| row.get(0),
            )
            .ok();

        if let Some(seq) = existing_seq {
            return Ok(seq);
        }

        // Monotonic sequence calculation
        let next_seq: i64 = tx.query_row(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM events",
            [],
            |row| row.get(0),
        )?;

        let payload_str = serde_json::to_string(&event.payload)?;
        let ts = if event.timestamp > 0.0 {
            event.timestamp
        } else {
            now_timestamp()
        };

        tx.execute(
            "INSERT INTO events (event_id, seq, name, trace_id, correlation_id, causation_id, trust_level, schema_version, timestamp, payload)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                event.event_id,
                next_seq,
                event.name,
                event.trace_id,
                event.correlation_id,
                event.causation_id,
                event.trust_level,
                event.schema_version,
                ts,
                payload_str
            ],
        )?;

        tx.commit()?;
        Ok(next_seq)
    }

    pub fn get_all(&self) -> Result<Vec<CivEvent>, EventStoreError> {
        self.query_events("SELECT event_id, seq, name, trace_id, correlation_id, causation_id, trust_level, schema_version, timestamp, payload FROM events ORDER BY seq ASC", params![])
    }

    pub fn get_by_name(&self, name: &str) -> Result<Vec<CivEvent>, EventStoreError> {
        self.query_events("SELECT event_id, seq, name, trace_id, correlation_id, causation_id, trust_level, schema_version, timestamp, payload FROM events WHERE name = ?1 ORDER BY seq ASC", params![name])
    }

    pub fn get_events_after(&self, after_seq: i64) -> Result<Vec<CivEvent>, EventStoreError> {
        self.query_events("SELECT event_id, seq, name, trace_id, correlation_id, causation_id, trust_level, schema_version, timestamp, payload FROM events WHERE seq > ?1 ORDER BY seq ASC", params![after_seq])
    }

    fn query_events(
        &self,
        sql: &str,
        params: &[&dyn rusqlite::ToSql],
    ) -> Result<Vec<CivEvent>, EventStoreError> {
        let conn = self.conn.lock().map_err(|_| EventStoreError::LockPoisoned)?;
        let mut stmt = conn.prepare(sql)?;
        let rows = stmt.query_map(params, |row| {
            let event_id: String = row.get(0)?;
            let seq: i64 = row.get(1)?;
            let name: String = row.get(2)?;
            let trace_id: Option<String> = row.get(3)?;
            let correlation_id: Option<String> = row.get(4)?;
            let causation_id: Option<String> = row.get(5)?;
            let trust_level: String = row.get(6)?;
            let schema_version: u32 = row.get(7)?;
            let timestamp: f64 = row.get(8)?;
            let payload_str: String = row.get(9)?;
            Ok((
                event_id,
                seq,
                name,
                trace_id,
                correlation_id,
                causation_id,
                trust_level,
                schema_version,
                timestamp,
                payload_str,
            ))
        })?;

        let mut events = Vec::new();
        for row in rows {
            let (
                event_id,
                seq,
                name,
                trace_id,
                correlation_id,
                causation_id,
                trust_level,
                schema_version,
                timestamp,
                payload_str,
            ) = row?;
            let payload: serde_json::Value =
                serde_json::from_str(&payload_str).unwrap_or(serde_json::Value::Null);
            events.push(CivEvent {
                event_id,
                seq,
                name,
                trace_id,
                correlation_id,
                causation_id,
                trust_level,
                schema_version,
                timestamp,
                payload,
            });
        }
        Ok(events)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_event_store_append_and_query() {
        let store = CivEventStore::in_memory().unwrap();
        let ev = CivEvent {
            event_id: "evt-1".to_string(),
            seq: 0,
            name: "civ.bot.created".to_string(),
            trace_id: None,
            correlation_id: Some("corr-1".to_string()),
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now_timestamp(),
            payload: serde_json::json!({"bot_id": "architect"}),
        };

        let seq = store.append(&ev).unwrap();
        assert_eq!(seq, 1);

        // Idempotent retry returns existing seq
        let seq2 = store.append(&ev).unwrap();
        assert_eq!(seq2, 1);

        let all = store.get_all().unwrap();
        assert_eq!(all.len(), 1);
        assert_eq!(all[0].event_id, "evt-1");
        assert_eq!(all[0].name, "civ.bot.created");
    }
}
