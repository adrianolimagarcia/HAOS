//! Ingestão de Alta Velocidade e Broadcast de Eventos via Tokio (Zero GIL / Zero Lock Contention).
//!
//! Fornece um canal de broadcast in-memory para envio imediato aos clientes (SSE/WebSockets)
//! e persiste em lote (batch write) em `events.db`.

use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicI64, Ordering};
use tokio::sync::broadcast;

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct PlatformEvent {
    #[serde(default)]
    pub event_id: Option<String>,
    pub name: String,
    pub payload: serde_json::Value,
    #[serde(default)]
    pub trace_id: Option<String>,
    #[serde(default)]
    pub correlation_id: Option<String>,
    #[serde(default)]
    pub causation_id: Option<String>,
    #[serde(default)]
    pub trust_level: Option<String>,
    #[serde(default)]
    pub schema_version: Option<i64>,
    #[serde(default)]
    pub timestamp: Option<f64>,
    #[serde(default)]
    pub seq: Option<i64>,
}

#[derive(Clone)]
pub struct EventHub {
    pub sender: broadcast::Sender<PlatformEvent>,
    pub db_path: PathBuf,
    pub next_seq: std::sync::Arc<AtomicI64>,
}

impl EventHub {
    pub fn new(db_path: PathBuf) -> Self {
        let (sender, _) = broadcast::channel(2048);
        let initial_seq = std::fs::read(&db_path)
            .ok()
            .and_then(|_| {
                Connection::open_with_flags(
                    &db_path,
                    OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
                )
                .ok()
            })
            .and_then(|conn| {
                conn.query_row("SELECT COALESCE(MAX(seq), 0) FROM events", [], |r| {
                    r.get::<_, i64>(0)
                })
                .ok()
            })
            .unwrap_or(0);
        let hub = Self {
            sender,
            db_path,
            next_seq: std::sync::Arc::new(AtomicI64::new(initial_seq)),
        };
        hub.spawn_writer_task();
        hub
    }

    /// Dispara worker em background para gravar eventos em lote sem travar threads HTTP
    fn spawn_writer_task(&self) {
        let mut rx = self.sender.subscribe();
        let db_path = self.db_path.clone();

        tokio::spawn(async move {
            let mut batch = Vec::with_capacity(64);
            let mut interval = tokio::time::interval(tokio::time::Duration::from_millis(50));

            loop {
                tokio::select! {
                    Ok(evt) = rx.recv() => {
                        batch.push(evt);
                        if batch.len() >= 64 {
                            Self::flush_batch(&db_path, &mut batch);
                        }
                    }
                    _ = interval.tick() => {
                        if !batch.is_empty() {
                            Self::flush_batch(&db_path, &mut batch);
                        }
                    }
                }
            }
        });
    }

    pub fn flush_batch(db_path: &Path, batch: &mut Vec<PlatformEvent>) {
        if !db_path.exists() {
            if let Some(parent) = db_path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
        }

        if let Ok(mut conn) = Connection::open_with_flags(
            db_path,
            OpenFlags::SQLITE_OPEN_READ_WRITE
                | OpenFlags::SQLITE_OPEN_CREATE
                | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        ) {
            let _ = conn.execute("PRAGMA journal_mode=WAL;", []);
            let _ = conn.execute("PRAGMA synchronous=NORMAL;", []);
            let _ = conn.execute(
                "CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    seq INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    correlation_id TEXT,
                    causation_id TEXT,
                    trust_level TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    payload TEXT NOT NULL
                );",
                [],
            );
            let _ = conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_seq_unique ON events(seq);",
                [],
            );

            // Descobre o próximo seq de forma atômica
            let current_seq: i64 = conn
                .query_row("SELECT COALESCE(MAX(seq), 0) FROM events", [], |r| r.get(0))
                .unwrap_or(0);
            let mut next_seq = current_seq;

            if let Ok(tx) = conn.transaction() {
                for evt in batch.drain(..) {
                    let ts = evt.timestamp.unwrap_or_else(|| {
                        std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap_or_default()
                            .as_secs_f64()
                    });
                    let stored_seq = evt.seq.unwrap_or_else(|| {
                        next_seq += 1;
                        next_seq
                    });
                    next_seq = next_seq.max(stored_seq);
                    let evt_id = evt
                        .event_id
                        .unwrap_or_else(|| format!("evt_{}_{}", stored_seq, (ts * 1000.0) as u64));
                    let trace = evt.trace_id.unwrap_or_else(|| "trace_auto".to_string());
                    let trust = evt.trust_level.unwrap_or_else(|| "system".to_string());
                    let schema_ver = evt.schema_version.unwrap_or(1);
                    let payload_str = evt.payload.to_string();

                    let _ = tx.execute(
                        "INSERT OR IGNORE INTO events (event_id, seq, name, trace_id, correlation_id, causation_id, trust_level, schema_version, timestamp, payload) \
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10);",
                        rusqlite::params![
                            evt_id,
                            next_seq,
                            evt.name,
                            trace,
                            evt.correlation_id,
                            evt.causation_id,
                            trust,
                            schema_ver,
                            ts,
                            payload_str
                        ],
                    );
                }
                let _ = tx.commit();
            }
        }
    }

    pub fn publish(&self, mut evt: PlatformEvent) -> Result<(), String> {
        let seq = self.next_seq.fetch_add(1, Ordering::SeqCst) + 1;
        evt.seq = Some(seq);
        if evt.event_id.is_none() {
            evt.event_id = Some(format!("evt-{seq}"));
        }
        if evt.timestamp.is_none() {
            evt.timestamp = Some(
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_secs_f64(),
            );
        }
        let _ = self.sender.send(evt);
        Ok(())
    }
}
