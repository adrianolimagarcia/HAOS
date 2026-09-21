//! Módulo nativo Rust para System-1 Decisions Fast-Path (<0.1ms).
//!
//! Permite que decisões memorizadas sejam consultadas e gravadas diretamente via SQLite WAL
//! sem travar o GIL do Python e com concorrência máxima.

use rusqlite::{params, Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct DecisionRecord {
    pub pattern_key: String,
    pub domain: String,
    pub raw_signature: String,
    pub decision_type: String,
    pub result: serde_json::Value,
    pub confidence: f64,
    pub hit_count: i64,
    pub created_at: f64,
    pub last_hit_at: f64,
}

pub struct SystemOneEngine {
    db_path: PathBuf,
}

impl SystemOneEngine {
    pub fn new(data_dir: &Path) -> Self {
        let db_path = data_dir.join("system_one_decisions.db");
        let engine = Self { db_path };
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
        if let Ok(conn) = Connection::open(&self.db_path) {
            let _ = conn.execute_batch(
                "PRAGMA journal_mode=WAL;
                 PRAGMA synchronous=NORMAL;
                 CREATE TABLE IF NOT EXISTS system_one_decisions (
                     pattern_key TEXT PRIMARY KEY,
                     domain TEXT NOT NULL,
                     raw_signature TEXT NOT NULL,
                     decision_type TEXT NOT NULL,
                     result_json TEXT NOT NULL,
                     confidence REAL NOT NULL,
                     hit_count INTEGER NOT NULL DEFAULT 1,
                     created_at REAL NOT NULL,
                     last_hit_at REAL NOT NULL
                 );
                 CREATE INDEX IF NOT EXISTS idx_decisions_domain ON system_one_decisions(domain);
                 CREATE INDEX IF NOT EXISTS idx_decisions_hits ON system_one_decisions(hit_count DESC);"
            );
        }
    }

    pub fn get_decision(&self, key: &str) -> Option<DecisionRecord> {
        let conn = Connection::open_with_flags(
            &self.db_path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        ).ok()?;

        let mut stmt = conn.prepare(
            "SELECT pattern_key, domain, raw_signature, decision_type, result_json,
                    confidence, hit_count, created_at, last_hit_at
             FROM system_one_decisions WHERE pattern_key = ?1"
        ).ok()?;

        let record = stmt.query_row(params![key], |row| {
            let res_str: String = row.get(4)?;
            let res_val = serde_json::from_str(&res_str).unwrap_or(serde_json::Value::Null);
            Ok(DecisionRecord {
                pattern_key: row.get(0)?,
                domain: row.get(1)?,
                raw_signature: row.get(2)?,
                decision_type: row.get(3)?,
                result: res_val,
                confidence: row.get(5)?,
                hit_count: row.get(6)?,
                created_at: row.get(7)?,
                last_hit_at: row.get(8)?,
            })
        }).ok()?;

        // Incrementa hit_count atomicamente
        let now = Self::now_secs();
        let _ = conn.execute(
            "UPDATE system_one_decisions SET hit_count = hit_count + 1, last_hit_at = ?1 WHERE pattern_key = ?2",
            params![now, key],
        );

        Some(record)
    }

    pub fn save_decision(&self, record: &DecisionRecord) -> Result<(), String> {
        let conn = Connection::open_with_flags(
            &self.db_path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        ).map_err(|e| e.to_string())?;

        let now = Self::now_secs();
        let res_json = serde_json::to_string(&record.result).unwrap_or_else(|_| "{}".to_string());

        conn.execute(
            "INSERT INTO system_one_decisions (
                pattern_key, domain, raw_signature, decision_type, result_json,
                confidence, hit_count, created_at, last_hit_at
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, 1, ?7, ?7)
            ON CONFLICT(pattern_key) DO UPDATE SET
                domain = excluded.domain,
                raw_signature = excluded.raw_signature,
                decision_type = excluded.decision_type,
                result_json = excluded.result_json,
                confidence = excluded.confidence,
                hit_count = hit_count + 1,
                last_hit_at = excluded.last_hit_at",
            params![
                record.pattern_key,
                record.domain,
                record.raw_signature,
                record.decision_type,
                res_json,
                record.confidence,
                now
            ],
        ).map_err(|e| e.to_string())?;

        Ok(())
    }

    pub fn list_decisions(&self, domain: Option<&str>, limit: usize) -> Vec<DecisionRecord> {
        let Ok(conn) = Connection::open_with_flags(
            &self.db_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        ) else {
            return Vec::new();
        };

        let mut out = Vec::new();
        if let Some(dom) = domain {
            if let Ok(mut stmt) = conn.prepare(
                "SELECT pattern_key, domain, raw_signature, decision_type, result_json,
                        confidence, hit_count, created_at, last_hit_at
                 FROM system_one_decisions WHERE domain = ?1 ORDER BY hit_count DESC LIMIT ?2"
            ) {
                if let Ok(rows) = stmt.query_map(params![dom, limit as i64], |row| {
                    let res_str: String = row.get(4)?;
                    let res_val = serde_json::from_str(&res_str).unwrap_or(serde_json::Value::Null);
                    Ok(DecisionRecord {
                        pattern_key: row.get(0)?,
                        domain: row.get(1)?,
                        raw_signature: row.get(2)?,
                        decision_type: row.get(3)?,
                        result: res_val,
                        confidence: row.get(5)?,
                        hit_count: row.get(6)?,
                        created_at: row.get(7)?,
                        last_hit_at: row.get(8)?,
                    })
                }) {
                    for r in rows.flatten() {
                        out.push(r);
                    }
                }
            }
        } else if let Ok(mut stmt) = conn.prepare(
            "SELECT pattern_key, domain, raw_signature, decision_type, result_json,
                    confidence, hit_count, created_at, last_hit_at
             FROM system_one_decisions ORDER BY hit_count DESC LIMIT ?1"
        ) {
            if let Ok(rows) = stmt.query_map(params![limit as i64], |row| {
                let res_str: String = row.get(4)?;
                let res_val = serde_json::from_str(&res_str).unwrap_or(serde_json::Value::Null);
                Ok(DecisionRecord {
                    pattern_key: row.get(0)?,
                    domain: row.get(1)?,
                    raw_signature: row.get(2)?,
                    decision_type: row.get(3)?,
                    result: res_val,
                    confidence: row.get(5)?,
                    hit_count: row.get(6)?,
                    created_at: row.get(7)?,
                    last_hit_at: row.get(8)?,
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
