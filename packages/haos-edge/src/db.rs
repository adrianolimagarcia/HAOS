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
        if let Ok(p) = std::env::var("HAOS_DATA_DIR") {
            let pb = PathBuf::from(p.trim());
            if pb.exists() {
                return pb;
            }
        }
        if let Ok(p) = std::env::var("HAOS_HOME") {
            let pb = PathBuf::from(p.trim());
            if pb.exists() {
                return pb;
            }
        }
        if let Ok(p) = std::env::var("HERMES_HOME") {
            let pb = PathBuf::from(p.trim());
            if pb.exists() {
                return pb;
            }
        }
        let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
        PathBuf::from(&home).join(".haos")
    }

    pub fn get_state_payload(data_dir: &std::path::Path) -> serde_json::Value {
        let kanban_path = data_dir.join("kanban.db");
        let events_path = data_dir.join("events.db");
        let settings_path = data_dir.join("settings.json");

        // 1. Taskboard stats
        let mut columns = Vec::new();
        let mut recent_tasks = Vec::new();
        let mut total_tasks = 0i64;

        if kanban_path.exists() {
            if let Ok(conn) = Connection::open_with_flags(
                &kanban_path,
                OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
            ) {
                // Group by status
                if let Ok(mut stmt) = conn.prepare("SELECT status, count(*) FROM tasks GROUP BY status;") {
                    if let Ok(rows) = stmt.query_map([], |r| {
                        Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))
                    }) {
                        for row in rows.flatten() {
                            total_tasks += row.1;
                            columns.push(serde_json::json!({
                                "status": row.0,
                                "count": row.1,
                            }));
                        }
                    }
                }

                // Recent tasks
                if let Ok(mut stmt) = conn.prepare(
                    "SELECT id, title, status, priority, assignee, created_at, started_at, completed_at
                     FROM tasks ORDER BY created_at DESC LIMIT 20;"
                ) {
                    if let Ok(rows) = stmt.query_map([], |r| {
                        Ok(serde_json::json!({
                            "id": r.get::<_, String>(0)?,
                            "title": r.get::<_, String>(1)?,
                            "status": r.get::<_, String>(2)?,
                            "priority": r.get::<_, i32>(3)?,
                            "assignee": r.get::<_, Option<String>>(4)?,
                            "created_at": r.get::<_, Option<i64>>(5)?,
                            "started_at": r.get::<_, Option<i64>>(6)?,
                            "completed_at": r.get::<_, Option<i64>>(7)?,
                        }))
                    }) {
                        for r in rows.flatten() {
                            recent_tasks.push(r);
                        }
                    }
                }
            }
        }

        // 2. Events tail
        let mut events_tail = Vec::new();
        if events_path.exists() {
            if let Ok(conn) = Connection::open_with_flags(
                &events_path,
                OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
            ) {
                if let Ok(mut stmt) = conn.prepare(
                    "SELECT name, timestamp, trace_id, payload FROM events ORDER BY seq DESC LIMIT 50;"
                ) {
                    if let Ok(rows) = stmt.query_map([], |r| {
                        Ok(serde_json::json!({
                            "name": r.get::<_, String>(0)?,
                            "timestamp": r.get::<_, f64>(1)?,
                            "trace_id": r.get::<_, String>(2)?,
                            "payload": r.get::<_, String>(3)?,
                        }))
                    }) {
                        for r in rows.flatten() {
                            events_tail.push(r);
                        }
                    }
                }
            }
        }

        // 3. Settings
        let settings: serde_json::Value = if settings_path.exists() {
            std::fs::read_to_string(&settings_path)
                .ok()
                .and_then(|s| serde_json::from_str(&s).ok())
                .unwrap_or_else(|| serde_json::json!({}))
        } else {
            serde_json::json!({
                "max_global_concurrency": 8,
                "auto_dispatch": true,
                "provider_limits": { "openai": 4, "anthropic": 4, "a6api": 4, "fallback": 2 }
            })
        };

        // 4. Assemble payload
        serde_json::json!({
            "taskboard": {
                "view": "taskboard",
                "columns": columns,
                "recent": recent_tasks,
                "total": total_tasks,
            },
            "events_tail": events_tail,
            "settings": settings,
            "concurrency": {
                "view": "concurrency",
                "active_global": 0,
                "max_global": settings.get("max_global_concurrency").and_then(|v| v.as_i64()).unwrap_or(8),
                "available_global": settings.get("max_global_concurrency").and_then(|v| v.as_i64()).unwrap_or(8),
                "providers": settings.get("provider_limits").cloned().unwrap_or_else(|| serde_json::json!({})),
                "active_tasks": [],
            },
            "critical_path": {
                "total_tasks_evaluated": total_tasks,
                "critical_path_ids": [],
                "inherited_priorities": {},
            },
            "approvals": {
                "decision": true,
                "pending": [],
            },
            "memory_graph": {
                "trace_edges": 0,
                "correlation_edges": 0,
                "nodes": [],
            },
            "meta": {
                "data_dir": data_dir.display().to_string(),
                "tasks_db": kanban_path.display().to_string(),
                "events_db": events_path.display().to_string(),
                "kanban_available": kanban_path.exists(),
                "mode": "haos-edge-rust",
            },
            "team_graph": {
                "organizational_hierarchy": { "agents": [], "councils": [] }
            },
            "agent_hierarchy": { "agents": [], "councils": [] },
            "evolution_pending": [],
            "evolution_history": [],
            "harness_bindings": [],
            "harness_catalog": ["dsh", "opencode", "claude_code", "gemini_cli", "cursor", "windsurf", "codex", "hermes_native"]
        })
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
