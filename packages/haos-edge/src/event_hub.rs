//! Ingestão de Alta Velocidade e Broadcast de Eventos via Tokio (Zero GIL / Zero Lock Contention).
//!
//! Fornece um canal de broadcast in-memory para envio imediato aos clientes (SSE/WebSockets)
//! e persiste em lote (batch write) em `events.db`.

use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use tokio::sync::broadcast;

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct PlatformEvent {
    pub name: String,
    pub payload: serde_json::Value,
    #[serde(default)]
    pub trace_id: Option<String>,
    #[serde(default)]
    pub timestamp: Option<f64>,
}

#[derive(Clone)]
pub struct EventHub {
    pub sender: broadcast::Sender<PlatformEvent>,
    pub db_path: PathBuf,
}

impl EventHub {
    pub fn new(db_path: PathBuf) -> Self {
        let (sender, _) = broadcast::channel(2048);
        let hub = Self { sender, db_path };
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

    fn flush_batch(db_path: &Path, batch: &mut Vec<PlatformEvent>) {
        if !db_path.exists() {
            if let Some(parent) = db_path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
        }

        if let Ok(mut conn) = Connection::open_with_flags(
            db_path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_CREATE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        ) {
            let _ = conn.execute(
                "CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    trace_id TEXT,
                    payload TEXT NOT NULL
                );",
                [],
            );

            if let Ok(tx) = conn.transaction() {
                for evt in batch.drain(..) {
                    let ts = evt.timestamp.unwrap_or_else(|| {
                        std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap_or_default()
                            .as_secs_f64()
                    });
                    let payload_str = evt.payload.to_string();
                    let _ = tx.execute(
                        "INSERT INTO events (name, timestamp, trace_id, payload) VALUES (?1, ?2, ?3, ?4);",
                        rusqlite::params![evt.name, ts, evt.trace_id, payload_str],
                    );
                }
                let _ = tx.commit();
            }
        }
    }

    pub fn publish(&self, evt: PlatformEvent) -> Result<(), String> {
        let _ = self.sender.send(evt);
        Ok(())
    }
}
