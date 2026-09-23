//! Módulo nativo Rust para Idempotência e Response Store do Gateway Inbound.
//!
//! Garante verificação atômica de mensagens duplicadas de plataformas (Telegram/Slack/Discord)
//! e respostas cacheadas com zero concorrência de GIL.

use rusqlite::{params, Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct IdempotencyRecord {
    pub scope: String,
    pub idempotency_key: String,
    pub fingerprint: String,
    pub run_id: String,
    pub status: serde_json::Value,
    pub created_at: f64,
    pub updated_at: f64,
}

pub struct IdempotencyEngine {
    idemp_db: PathBuf,
    resp_db: PathBuf,
}

impl IdempotencyEngine {
    pub fn new(data_dir: &Path) -> Self {
        let idemp_db = data_dir.join("runs_idempotency.db");
        let resp_db = data_dir.join("response_store.db");
        let engine = Self { idemp_db, resp_db };
        engine.init_db();
        engine
    }

    fn now_secs() -> f64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0)
    }

    fn init_db(&self) {
        if let Ok(conn) = Connection::open(&self.idemp_db) {
            let _ = conn.execute_batch(
                "PRAGMA journal_mode=WAL;
                 PRAGMA synchronous=NORMAL;
                 CREATE TABLE IF NOT EXISTS run_idempotency (
                     scope TEXT NOT NULL,
                     idempotency_key TEXT NOT NULL,
                     fingerprint TEXT NOT NULL,
                     run_id TEXT NOT NULL,
                     status_json TEXT NOT NULL,
                     owner_pid INTEGER NOT NULL DEFAULT 0,
                     owner_started INTEGER NOT NULL DEFAULT 0,
                     retention_until REAL NOT NULL DEFAULT 0,
                     acknowledged_at REAL,
                     created_at REAL NOT NULL,
                     updated_at REAL NOT NULL,
                     PRIMARY KEY (scope, idempotency_key)
                 );
                 CREATE UNIQUE INDEX IF NOT EXISTS run_idempotency_run_id ON run_idempotency(run_id);"
            );
        }

        if let Ok(conn) = Connection::open(&self.resp_db) {
            let _ = conn.execute_batch(
                "PRAGMA journal_mode=WAL;
                 PRAGMA synchronous=NORMAL;
                 CREATE TABLE IF NOT EXISTS responses (
                     response_id TEXT PRIMARY KEY,
                     data TEXT NOT NULL,
                     accessed_at REAL NOT NULL
                 );
                 CREATE TABLE IF NOT EXISTS conversations (
                     name TEXT PRIMARY KEY,
                     response_id TEXT NOT NULL
                 );",
            );
        }
    }

    /// Verifica se uma requisição/mensagem já foi processada
    pub fn check_idempotency(&self, scope: &str, key: &str) -> Option<IdempotencyRecord> {
        let conn = Connection::open_with_flags(
            &self.idemp_db,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .ok()?;

        let mut stmt = conn.prepare(
            "SELECT scope, idempotency_key, fingerprint, run_id, status_json, created_at, updated_at
             FROM run_idempotency WHERE scope = ?1 AND idempotency_key = ?2"
        ).ok()?;

        stmt.query_row(params![scope, key], |row| {
            let st_str: String = row.get(4)?;
            let st_val = serde_json::from_str(&st_str).unwrap_or(serde_json::Value::Null);
            Ok(IdempotencyRecord {
                scope: row.get(0)?,
                idempotency_key: row.get(1)?,
                fingerprint: row.get(2)?,
                run_id: row.get(3)?,
                status: st_val,
                created_at: row.get(5)?,
                updated_at: row.get(6)?,
            })
        })
        .ok()
    }

    /// Registra ou atualiza um run com chave de idempotência
    pub fn record_idempotency(
        &self,
        scope: &str,
        key: &str,
        fingerprint: &str,
        run_id: &str,
        status: &serde_json::Value,
    ) -> Result<(), String> {
        let conn = Connection::open_with_flags(
            &self.idemp_db,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|e| e.to_string())?;

        let now = Self::now_secs();
        let st_json = serde_json::to_string(status).unwrap_or_else(|_| "{}".to_string());

        conn.execute(
            "INSERT INTO run_idempotency (
                scope, idempotency_key, fingerprint, run_id, status_json,
                owner_pid, owner_started, retention_until, created_at, updated_at
            ) VALUES (?1, ?2, ?3, ?4, ?5, 0, 0, ?6, ?7, ?7)
            ON CONFLICT(scope, idempotency_key) DO UPDATE SET
                fingerprint = excluded.fingerprint,
                run_id = excluded.run_id,
                status_json = excluded.status_json,
                updated_at = excluded.updated_at",
            params![scope, key, fingerprint, run_id, st_json, now + 86400.0, now],
        )
        .map_err(|e| e.to_string())?;

        Ok(())
    }

    /// Busca resposta cacheada no response_store
    pub fn get_response(&self, resp_id: &str) -> Option<String> {
        let conn = Connection::open_with_flags(
            &self.resp_db,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .ok()?;

        let mut stmt = conn
            .prepare("SELECT data FROM responses WHERE response_id = ?1")
            .ok()?;
        let data: String = stmt.query_row(params![resp_id], |row| row.get(0)).ok()?;

        let now = Self::now_secs();
        let _ = conn.execute(
            "UPDATE responses SET accessed_at = ?1 WHERE response_id = ?2",
            params![now, resp_id],
        );

        Some(data)
    }

    /// Salva resposta no response_store
    pub fn save_response(&self, resp_id: &str, data: &str) -> Result<(), String> {
        let conn = Connection::open_with_flags(
            &self.resp_db,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|e| e.to_string())?;

        let now = Self::now_secs();
        conn.execute(
            "INSERT INTO responses (response_id, data, accessed_at) VALUES (?1, ?2, ?3)
             ON CONFLICT(response_id) DO UPDATE SET data = excluded.data, accessed_at = excluded.accessed_at",
            params![resp_id, data, now],
        ).map_err(|e| e.to_string())?;

        Ok(())
    }
}
