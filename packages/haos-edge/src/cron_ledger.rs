//! Módulo nativo Rust para consulta e auditoria do Cron & Delivery Queue (<1ms).
//!
//! Exposto para Dashboards, WebUI e Control Plane lerem execuções e status de entregas
//! sem acordar o interpretador Python.

use rusqlite::{params, Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct CronExecution {
    pub id: String,
    pub job_id: String,
    pub source: String,
    pub status: String,
    pub claimed_at: String,
    pub started_at: Option<String>,
    pub finished_at: Option<String>,
    pub error: Option<String>,
    pub delivery_outcome: Option<String>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct CronDelivery {
    pub execution_id: String,
    pub job_json: serde_json::Value,
    pub content: String,
    pub status: String,
    pub created_at: String,
    pub finished_at: Option<String>,
    pub error: Option<String>,
}

pub struct CronLedgerEngine {
    exec_db: PathBuf,
    deliv_db: PathBuf,
}

impl CronLedgerEngine {
    pub fn new(data_dir: &Path) -> Self {
        let exec_db = data_dir.join("cron").join("executions.db");
        let deliv_db = data_dir.join("cron").join("deliveries.db");
        Self { exec_db, deliv_db }
    }

    pub fn list_executions(&self, job_id: Option<&str>, limit: usize) -> Vec<CronExecution> {
        let Ok(conn) = Connection::open_with_flags(
            &self.exec_db,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        ) else {
            return Vec::new();
        };

        let mut out = Vec::new();
        if let Some(jid) = job_id {
            if let Ok(mut stmt) = conn.prepare(
                "SELECT id, job_id, source, status, claimed_at, started_at, finished_at, error, delivery_outcome
                 FROM executions WHERE job_id = ?1 ORDER BY claimed_at DESC, id DESC LIMIT ?2"
            ) {
                if let Ok(rows) = stmt.query_map(params![jid, limit as i64], |row| {
                    Ok(CronExecution {
                        id: row.get(0)?,
                        job_id: row.get(1)?,
                        source: row.get(2)?,
                        status: row.get(3)?,
                        claimed_at: row.get(4)?,
                        started_at: row.get(5)?,
                        finished_at: row.get(6)?,
                        error: row.get(7)?,
                        delivery_outcome: row.get(8)?,
                    })
                }) {
                    for r in rows.flatten() {
                        out.push(r);
                    }
                }
            }
        } else if let Ok(mut stmt) = conn.prepare(
            "SELECT id, job_id, source, status, claimed_at, started_at, finished_at, error, delivery_outcome
             FROM executions ORDER BY claimed_at DESC, id DESC LIMIT ?1"
        ) {
            if let Ok(rows) = stmt.query_map(params![limit as i64], |row| {
                Ok(CronExecution {
                    id: row.get(0)?,
                    job_id: row.get(1)?,
                    source: row.get(2)?,
                    status: row.get(3)?,
                    claimed_at: row.get(4)?,
                    started_at: row.get(5)?,
                    finished_at: row.get(6)?,
                    error: row.get(7)?,
                    delivery_outcome: row.get(8)?,
                })
            }) {
                for r in rows.flatten() {
                    out.push(r);
                }
            }
        }
        out
    }

    pub fn list_pending_deliveries(&self, limit: usize) -> Vec<CronDelivery> {
        let Ok(conn) = Connection::open_with_flags(
            &self.deliv_db,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        ) else {
            return Vec::new();
        };

        let mut out = Vec::new();
        if let Ok(mut stmt) = conn.prepare(
            "SELECT execution_id, job_json, content, status, created_at, finished_at, error
             FROM deliveries WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?1",
        ) {
            if let Ok(rows) = stmt.query_map(params![limit as i64], |row| {
                let job_raw: String = row.get(1)?;
                let job_val = serde_json::from_str(&job_raw).unwrap_or(serde_json::Value::Null);
                Ok(CronDelivery {
                    execution_id: row.get(0)?,
                    job_json: job_val,
                    content: row.get(2)?,
                    status: row.get(3)?,
                    created_at: row.get(4)?,
                    finished_at: row.get(5)?,
                    error: row.get(6)?,
                })
            }) {
                for r in rows.flatten() {
                    out.push(r);
                }
            }
        }
        out
    }
}
