//! Kanban Candidate Selector (Read-Only)
//!
//! Provides deterministic candidate enumeration from kanban.db SQLite database.
//! Adheres strictly to the read-only contract: NO mutations, NO locks, NO spawns.
//! Contract Version: kanban_selector_v1

use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CandidateTask {
    pub id: String,
    pub assignee: Option<String>,
    pub lane: String,
    pub status: String,
    pub priority: i64,
    pub created_at: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SelectorResult {
    pub contract_version: String,
    pub ok: bool,
    pub candidates: Vec<CandidateTask>,
    pub error: Option<String>,
}

impl SelectorResult {
    pub fn error(msg: String) -> Self {
        Self {
            contract_version: "kanban_selector_v1".to_string(),
            ok: false,
            candidates: Vec::new(),
            error: Some(msg),
        }
    }
}

/// Query ready and review candidates in canonical dispatch order:
/// ORDER BY priority DESC, created_at ASC
///
/// Matches Python's `_lane_rows(conn, 'ready')` and `_lane_rows(conn, 'review')`.
pub fn select_candidates(
    db_path: &Path,
    include_review: bool,
) -> Result<Vec<CandidateTask>, String> {
    if !db_path.exists() {
        return Err(format!("Database does not exist: {}", db_path.display()));
    }

    // Explicitly open read-only so SQLite engine refuses any accidental writes
    let conn = Connection::open_with_flags(
        db_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|e| format!("Failed to open db in read-only mode: {}", e))?;

    let mut candidates = Vec::new();

    // 1. Ready lane
    {
        let mut stmt = conn
            .prepare(
                "SELECT id, assignee, status, priority, created_at FROM tasks \
                 WHERE status = 'ready' AND claim_lock IS NULL \
                 ORDER BY priority DESC, created_at ASC, id ASC",
            )
            .map_err(|e| format!("Failed to prepare ready query: {}", e))?;

        let rows = stmt
            .query_map([], |row| {
                let st: String = row.get(2)?;
                Ok(CandidateTask {
                    id: row.get(0)?,
                    assignee: row.get(1)?,
                    lane: st.clone(),
                    status: st,
                    priority: row.get(3)?,
                    created_at: row.get(4)?,
                })
            })
            .map_err(|e| format!("Failed to query ready tasks: {}", e))?;

        for item in rows {
            candidates.push(item.map_err(|e| format!("Row map error: {}", e))?);
        }
    }

    // 2. Review lane (if enabled)
    if include_review {
        let mut stmt = conn
            .prepare(
                "SELECT id, assignee, status, priority, created_at FROM tasks \
                 WHERE status = 'review' AND claim_lock IS NULL \
                 ORDER BY priority DESC, created_at ASC, id ASC",
            )
            .map_err(|e| format!("Failed to prepare review query: {}", e))?;

        let rows = stmt
            .query_map([], |row| {
                let st: String = row.get(2)?;
                Ok(CandidateTask {
                    id: row.get(0)?,
                    assignee: row.get(1)?,
                    lane: st.clone(),
                    status: st,
                    priority: row.get(3)?,
                    created_at: row.get(4)?,
                })
            })
            .map_err(|e| format!("Failed to query review tasks: {}", e))?;

        for item in rows {
            candidates.push(item.map_err(|e| format!("Row map error: {}", e))?);
        }
    }

    Ok(candidates)
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;
    use tempfile::NamedTempFile;

    fn setup_test_db(file: &NamedTempFile) -> Connection {
        let conn = Connection::open(file.path()).unwrap();
        conn.execute_batch(
            r#"
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                assignee TEXT,
                status TEXT NOT NULL,
                priority INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL,
                claim_lock TEXT
            );
            "#,
        )
        .unwrap();
        conn
    }

    #[test]
    fn test_select_candidates_ordering_and_filtering() {
        let tmp = NamedTempFile::new().unwrap();
        let conn = setup_test_db(&tmp);

        // Insert tasks with different priorities, creation times and statuses
        conn.execute(
            "INSERT INTO tasks (id, title, assignee, status, priority, created_at, claim_lock) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            rusqlite::params!["task-low", "Low", "worker1", "ready", 0, 1000, Option::<String>::None],
        ).unwrap();
        conn.execute(
            "INSERT INTO tasks (id, title, assignee, status, priority, created_at, claim_lock) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            rusqlite::params!["task-high", "High", "worker1", "ready", 10, 2000, Option::<String>::None],
        ).unwrap();
        conn.execute(
            "INSERT INTO tasks (id, title, assignee, status, priority, created_at, claim_lock) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            rusqlite::params!["task-high-older", "High Older", "worker2", "ready", 10, 1500, Option::<String>::None],
        ).unwrap();
        conn.execute(
            "INSERT INTO tasks (id, title, assignee, status, priority, created_at, claim_lock) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            rusqlite::params!["task-claimed", "Claimed", "worker1", "ready", 20, 500, "locked-by-someone"],
        ).unwrap();
        conn.execute(
            "INSERT INTO tasks (id, title, assignee, status, priority, created_at, claim_lock) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            rusqlite::params!["task-todo", "Todo", "worker1", "todo", 30, 500, Option::<String>::None],
        ).unwrap();
        conn.execute(
            "INSERT INTO tasks (id, title, assignee, status, priority, created_at, claim_lock) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            rusqlite::params!["task-review", "Review", "reviewer", "review", 5, 1200, Option::<String>::None],
        ).unwrap();

        // 1. Without review
        let candidates = select_candidates(tmp.path(), false).unwrap();
        assert_eq!(candidates.len(), 3);
        // priority 10, created 1500 first
        assert_eq!(candidates[0].id, "task-high-older");
        // priority 10, created 2000 second
        assert_eq!(candidates[1].id, "task-high");
        // priority 0, created 1000 third
        assert_eq!(candidates[2].id, "task-low");

        // 2. With review
        let candidates_with_rev = select_candidates(tmp.path(), true).unwrap();
        assert_eq!(candidates_with_rev.len(), 4);
        assert_eq!(candidates_with_rev[3].id, "task-review");
        assert_eq!(candidates_with_rev[3].lane, "review");
    }

    #[test]
    fn test_select_candidates_missing_db() {
        let missing = Path::new("/tmp/non_existent_kanban_db_12345.db");
        let res = select_candidates(missing, false);
        assert!(res.is_err());
    }
}
