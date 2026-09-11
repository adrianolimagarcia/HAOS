use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Serialize, Deserialize, Debug)]
pub struct TaskCard {
    pub id: String,
    pub title: String,
    pub status: String,
    pub priority: i32,
    pub assignee: Option<String>,
}

pub struct DbHelper;

impl DbHelper {
    pub fn get_haos_home() -> PathBuf {
        if let Ok(p) = std::env::var("HAOS_HOME") {
            if !p.trim().is_empty() {
                return PathBuf::from(p.trim());
            }
        }
        let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
        let candidate = PathBuf::from(&home).join(".haos");
        candidate
    }

    pub fn get_tasks() -> Result<Vec<TaskCard>, String> {
        let haos_home = Self::get_haos_home();
        let kanban_path = haos_home.join("kanban.db");
        if !kanban_path.exists() {
            return Ok(Vec::new());
        }

        let conn = Connection::open_with_flags(
            &kanban_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|e| format!("Failed to open kanban.db: {e}"))?;

        let mut stmt = conn
            .prepare("SELECT id, title, status, priority, assignee FROM tasks ORDER BY priority DESC LIMIT 50;")
            .map_err(|e| format!("Query prepare failed: {e}"))?;

        let rows = stmt
            .query_map([], |row| {
                Ok(TaskCard {
                    id: row.get(0)?,
                    title: row.get(1)?,
                    status: row.get(2)?,
                    priority: row.get(3)?,
                    assignee: row.get(4)?,
                })
            })
            .map_err(|e| format!("Query exec failed: {e}"))?;

        let mut tasks = Vec::new();
        for r in rows.flatten() {
            tasks.push(r);
        }
        Ok(tasks)
    }

    pub fn search_ragflow(query_str: &str, limit: usize) -> Result<Vec<(String, String, String, String)>, String> {
        let haos_home = Self::get_haos_home();
        let db_path = haos_home.join("memory").join("ragflow.db");
        if !db_path.exists() {
            return Ok(Vec::new());
        }

        let conn = Connection::open_with_flags(
            &db_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|e| format!("Failed to open ragflow.db: {e}"))?;

        // FTS5 MATCH query
        let fts_tokens: Vec<&str> = query_str.split_whitespace().collect();
        let fts_match = fts_tokens
            .iter()
            .map(|t| format!("\"{}\"", t.replace('"', "")))
            .collect::<Vec<_>>()
            .join(" OR ");

        if fts_match.is_empty() {
            return Ok(Vec::new());
        }

        let mut stmt = conn
            .prepare(
                "SELECT c.id, c.doc_path, c.header_path, c.provenance_anchor, c.content
                 FROM haos_rag_fts f
                 JOIN haos_rag_chunks c ON f.id = c.id
                 WHERE haos_rag_fts MATCH ?
                 ORDER BY bm25(haos_rag_fts) ASC
                 LIMIT ?;",
            )
            .map_err(|e| format!("FTS5 prepare failed: {e}"))?;

        let rows = stmt
            .query_map([fts_match, limit.to_string()], |row| {
                Ok((
                    row.get::<_, String>(1)?, // doc_path
                    row.get::<_, String>(2)?, // header_path
                    row.get::<_, String>(3)?, // anchor
                    row.get::<_, String>(4)?, // content
                ))
            })
            .map_err(|e| format!("FTS5 query failed: {e}"))?;

        let mut results = Vec::new();
        for r in rows.flatten() {
            results.push(r);
        }
        Ok(results)
    }

    /// Conta linhas de uma tabela (None se o banco/tabela não existe). O nome da
    /// tabela é sempre literal interno — nunca vem de entrada do usuário.
    pub fn count_rows(db_path: &std::path::Path, table: &str) -> Option<i64> {
        if !db_path.exists() {
            return None;
        }
        let conn = Connection::open_with_flags(
            db_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .ok()?;
        conn.query_row(&format!("SELECT count(*) FROM {table};"), [], |r| r.get(0))
            .ok()
    }

    pub fn checkpoint_all_dbs() {
        let home = Self::get_haos_home();
        let candidate_dbs = [
            home.join("state.db"),
            home.join("kanban.db"),
            home.join("memory").join("ragflow.db"),
            home.join("memory").join("reconciled_memories.db"),
        ];

        for path in &candidate_dbs {
            if path.exists() {
                if let Ok(conn) = Connection::open(path) {
                    let _ = conn.execute_batch("PRAGMA wal_checkpoint(PASSIVE);");
                }
            }
        }
    }
}
