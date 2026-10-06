//! Kanban Atomic Claim & Lease Fencing (Phase 2)
//!
//! Provides deterministic and atomic claim and lease operations on kanban.db.
//! Guarantees:
//! - Immediate transactional isolation (no race conditions between concurrent workers)
//! - Dependency gating check (parents_satisfied)
//! - Atomic CAS (status -> 'running', claim_lock set only if currently NULL)
//! - Historical run recording in task_runs (current_run_id tracking)
//! - Append-only audit trail in task_events ('claimed', 'claim_rejected', 'dependency_wait')
//! Contract Version: kanban_claim_v1

use rusqlite::{params, Connection, OpenFlags, TransactionBehavior};
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

pub const CONTRACT_VERSION: &str = "kanban_claim_v1";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ClaimTaskResult {
    pub contract_version: String,
    pub ok: bool,
    pub claimed: bool,
    pub task_id: Option<String>,
    pub run_id: Option<i64>,
    pub status: Option<String>,
    pub claim_lock: Option<String>,
    pub claim_expires: Option<i64>,
    pub rejection_reason: Option<String>,
    pub error: Option<String>,
}

impl ClaimTaskResult {
    pub fn error(msg: String) -> Self {
        Self {
            contract_version: CONTRACT_VERSION.to_string(),
            ok: false,
            claimed: false,
            task_id: None,
            run_id: None,
            status: None,
            claim_lock: None,
            claim_expires: None,
            rejection_reason: None,
            error: Some(msg),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct HeartbeatResult {
    pub contract_version: String,
    pub ok: bool,
    pub renewed: bool,
    pub task_id: Option<String>,
    pub run_id: Option<i64>,
    pub claim_expires: Option<i64>,
    pub error: Option<String>,
}

impl HeartbeatResult {
    pub fn error(msg: String) -> Self {
        Self {
            contract_version: CONTRACT_VERSION.to_string(),
            ok: false,
            renewed: false,
            task_id: None,
            run_id: None,
            claim_expires: None,
            error: Some(msg),
        }
    }
}

fn current_epoch_seconds() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// Atomically claim a task from 'ready' or 'review' lane into 'running' status.
///
/// Parity contract with Python's `kanban_db.claim_task` and `claim_review_task`.
pub fn atomic_claim_task(
    db_path: &Path,
    task_id: &str,
    claimer: &str,
    ttl_seconds: i64,
    source_status: &str, // "ready" or "review"
) -> Result<ClaimTaskResult, String> {
    if !db_path.exists() {
        return Err(format!("Database does not exist: {}", db_path.display()));
    }

    if source_status != "ready" && source_status != "review" {
        return Err(format!(
            "Invalid source_status '{}'; expected 'ready' or 'review'",
            source_status
        ));
    }

    let mut conn = Connection::open_with_flags(
        db_path,
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|e| format!("Failed to open db for writing: {}", e))?;

    conn.busy_timeout(std::time::Duration::from_millis(5000))
        .map_err(|e| format!("Failed to set busy timeout: {}", e))?;

    let now = current_epoch_seconds();
    let expires = now + ttl_seconds;

    let tx = conn
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|e| format!("Failed to begin immediate transaction: {}", e))?;

    // 1. Dependency gating: check if direct parents are satisfied
    // Parity: SELECT 1 FROM task_links l JOIN tasks p ON p.id = l.parent_id
    //         WHERE l.child_id = ? AND p.status NOT IN ('done', 'archived') LIMIT 1
    let parent_unsatisfied: bool = {
        let mut stmt = tx
            .prepare(
                "SELECT 1 FROM task_links l \
                 JOIN tasks p ON p.id = l.parent_id \
                 WHERE l.child_id = ?1 AND p.status NOT IN ('done', 'archived') \
                 LIMIT 1",
            )
            .map_err(|e| format!("Failed to prepare parent dependency query: {}", e))?;
        stmt.exists(params![task_id])
            .map_err(|e| format!("Failed to execute parent dependency query: {}", e))?
    };

    if parent_unsatisfied {
        if source_status == "ready" {
            tx.execute(
                "UPDATE tasks SET status = 'todo' WHERE id = ?1 AND status = 'ready'",
                params![task_id],
            )
            .map_err(|e| format!("Failed to demote ready task to todo: {}", e))?;

            tx.execute(
                "INSERT INTO task_events (task_id, run_id, kind, payload, created_at) \
                 VALUES (?1, NULL, 'claim_rejected', '{\"reason\":\"parents_not_done\"}', ?2)",
                params![task_id, now],
            )
            .map_err(|e| format!("Failed to append claim_rejected event: {}", e))?;
        } else {
            tx.execute(
                "UPDATE tasks SET status = 'todo' WHERE id = ?1 AND status = 'review' AND claim_lock IS NULL",
                params![task_id],
            )
            .map_err(|e| format!("Failed to demote review task to todo: {}", e))?;

            tx.execute(
                "INSERT INTO task_events (task_id, run_id, kind, payload, created_at) \
                 VALUES (?1, NULL, 'dependency_wait', '{\"reason\":\"parent_reopened\",\"source_status\":\"review\"}', ?2)",
                params![task_id, now],
            )
            .map_err(|e| format!("Failed to append dependency_wait event: {}", e))?;
        }

        tx.commit()
            .map_err(|e| format!("Failed to commit dependency demotion: {}", e))?;

        return Ok(ClaimTaskResult {
            contract_version: CONTRACT_VERSION.to_string(),
            ok: true,
            claimed: false,
            task_id: Some(task_id.to_string()),
            run_id: None,
            status: Some("todo".to_string()),
            claim_lock: None,
            claim_expires: None,
            rejection_reason: Some(if source_status == "ready" {
                "parents_not_done".to_string()
            } else {
                "parent_reopened".to_string()
            }),
            error: None,
        });
    }

    // 2. Invariant recovery: close dangling run if ready re-claim
    if source_status == "ready" {
        let dangling_run_id: Option<i64> = {
            let mut stmt = tx
                .prepare("SELECT current_run_id FROM tasks WHERE id = ?1 AND status = 'ready'")
                .map_err(|e| format!("Failed to prepare dangling run query: {}", e))?;
            stmt.query_row(params![task_id], |row| row.get(0))
                .unwrap_or(None)
        };

        if let Some(stale_id) = dangling_run_id {
            tx.execute(
                "UPDATE task_runs \
                 SET status = 'reclaimed', outcome = 'reclaimed', \
                     summary = COALESCE(summary, 'invariant recovery on re-claim'), \
                     ended_at = COALESCE(ended_at, ?2) \
                 WHERE id = ?1 AND ended_at IS NULL",
                params![stale_id, now],
            )
            .map_err(|e| format!("Failed to mark stale run as reclaimed: {}", e))?;

            tx.execute(
                "UPDATE tasks SET current_run_id = NULL WHERE id = ?1",
                params![task_id],
            )
            .map_err(|e| format!("Failed to clear current_run_id on task: {}", e))?;
        }
    }

    // 3. Atomic CAS update on tasks
    let rows_updated = tx
        .execute(
            "UPDATE tasks \
             SET status = 'running', \
                 claim_lock = ?1, \
                 claim_expires = ?2, \
                 started_at = COALESCE(started_at, ?3) \
             WHERE id = ?4 \
               AND status = ?5 \
               AND claim_lock IS NULL",
            params![claimer, expires, now, task_id, source_status],
        )
        .map_err(|e| format!("Failed to execute CAS update on tasks: {}", e))?;

    if rows_updated != 1 {
        // CAS failed: either task not in source_status or already claimed
        tx.rollback().ok();
        return Ok(ClaimTaskResult {
            contract_version: CONTRACT_VERSION.to_string(),
            ok: true,
            claimed: false,
            task_id: Some(task_id.to_string()),
            run_id: None,
            status: None,
            claim_lock: None,
            claim_expires: None,
            rejection_reason: Some("cas_conflict_or_invalid_status".to_string()),
            error: None,
        });
    }

    // 4. Query task metadata for task_runs row
    let (assignee, max_runtime_seconds, current_step_key): (
        Option<String>,
        Option<i64>,
        Option<String>,
    ) = {
        let mut stmt = tx
            .prepare(
                "SELECT assignee, max_runtime_seconds, current_step_key FROM tasks WHERE id = ?1",
            )
            .map_err(|e| format!("Failed to prepare metadata select: {}", e))?;
        stmt.query_row(params![task_id], |row| {
            Ok((row.get(0)?, row.get(1)?, row.get(2)?))
        })
        .map_err(|e| format!("Failed to read task metadata: {}", e))?
    };

    // 5. Open new task_runs row
    tx.execute(
        "INSERT INTO task_runs ( \
             task_id, profile, step_key, status, \
             claim_lock, claim_expires, max_runtime_seconds, \
             started_at \
         ) VALUES (?1, ?2, ?3, 'running', ?4, ?5, ?6, ?7)",
        params![
            task_id,
            assignee,
            current_step_key,
            claimer,
            expires,
            max_runtime_seconds,
            now
        ],
    )
    .map_err(|e| format!("Failed to insert into task_runs: {}", e))?;

    let run_id = tx.last_insert_rowid();

    // 6. Denormalize current_run_id on tasks
    tx.execute(
        "UPDATE tasks SET current_run_id = ?1 WHERE id = ?2",
        params![run_id, task_id],
    )
    .map_err(|e| format!("Failed to update tasks.current_run_id: {}", e))?;

    // 7. Emit 'claimed' event
    let payload = if source_status == "review" {
        format!(
            "{{\"lock\":\"{}\",\"expires\":{},\"source_status\":\"review\"}}",
            claimer, expires
        )
    } else {
        format!("{{\"lock\":\"{}\",\"expires\":{}}}", claimer, expires)
    };

    tx.execute(
        "INSERT INTO task_events (task_id, run_id, kind, payload, created_at) \
         VALUES (?1, ?2, 'claimed', ?3, ?4)",
        params![task_id, run_id, payload, now],
    )
    .map_err(|e| format!("Failed to insert into task_events: {}", e))?;

    tx.commit()
        .map_err(|e| format!("Failed to commit claim transaction: {}", e))?;

    Ok(ClaimTaskResult {
        contract_version: CONTRACT_VERSION.to_string(),
        ok: true,
        claimed: true,
        task_id: Some(task_id.to_string()),
        run_id: Some(run_id),
        status: Some("running".to_string()),
        claim_lock: Some(claimer.to_string()),
        claim_expires: Some(expires),
        rejection_reason: None,
        error: None,
    })
}

/// Extend a running claim lease (heartbeat).
pub fn heartbeat_claim_task(
    db_path: &Path,
    task_id: &str,
    claimer: &str,
    ttl_seconds: i64,
) -> Result<HeartbeatResult, String> {
    if !db_path.exists() {
        return Err(format!("Database does not exist: {}", db_path.display()));
    }

    let mut conn = Connection::open_with_flags(
        db_path,
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|e| format!("Failed to open db for heartbeat: {}", e))?;

    conn.busy_timeout(std::time::Duration::from_millis(5000))
        .map_err(|e| format!("Failed to set busy timeout: {}", e))?;

    let now = current_epoch_seconds();
    let expires = now + ttl_seconds;

    let tx = conn
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|e| format!("Failed to begin heartbeat transaction: {}", e))?;

    let rows_updated = tx
        .execute(
            "UPDATE tasks SET claim_expires = ?1 \
             WHERE id = ?2 AND status = 'running' AND claim_lock = ?3",
            params![expires, task_id, claimer],
        )
        .map_err(|e| format!("Failed to update tasks claim_expires: {}", e))?;

    if rows_updated != 1 {
        tx.rollback().ok();
        return Ok(HeartbeatResult {
            contract_version: CONTRACT_VERSION.to_string(),
            ok: true,
            renewed: false,
            task_id: Some(task_id.to_string()),
            run_id: None,
            claim_expires: None,
            error: None,
        });
    }

    let run_id: Option<i64> = {
        let mut stmt = tx
            .prepare("SELECT current_run_id FROM tasks WHERE id = ?1")
            .map_err(|e| format!("Failed to prepare current_run_id query: {}", e))?;
        stmt.query_row(params![task_id], |row| row.get(0))
            .unwrap_or(None)
    };

    if let Some(rid) = run_id {
        tx.execute(
            "UPDATE task_runs SET claim_expires = ?1 WHERE id = ?2",
            params![expires, rid],
        )
        .map_err(|e| format!("Failed to update task_runs claim_expires: {}", e))?;
    }

    tx.commit()
        .map_err(|e| format!("Failed to commit heartbeat transaction: {}", e))?;

    Ok(HeartbeatResult {
        contract_version: CONTRACT_VERSION.to_string(),
        ok: true,
        renewed: true,
        task_id: Some(task_id.to_string()),
        run_id,
        claim_expires: Some(expires),
        error: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;
    use tempfile::NamedTempFile;

    fn setup_test_kanban_schema(file: &NamedTempFile) -> Connection {
        let conn = Connection::open(file.path()).unwrap();
        conn.execute_batch(
            r#"
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT,
                assignee TEXT,
                status TEXT NOT NULL,
                priority INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL,
                started_at INTEGER,
                completed_at INTEGER,
                workspace_kind TEXT NOT NULL DEFAULT 'scratch',
                claim_lock TEXT,
                claim_expires INTEGER,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                current_run_id INTEGER,
                current_step_key TEXT,
                max_runtime_seconds INTEGER
            );

            CREATE TABLE task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                profile TEXT,
                step_key TEXT,
                status TEXT NOT NULL,
                claim_lock TEXT,
                claim_expires INTEGER,
                max_runtime_seconds INTEGER,
                started_at INTEGER NOT NULL,
                ended_at INTEGER,
                outcome TEXT,
                summary TEXT
            );

            CREATE TABLE task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                run_id INTEGER,
                kind TEXT NOT NULL,
                payload TEXT,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE task_links (
                parent_id TEXT NOT NULL,
                child_id TEXT NOT NULL,
                PRIMARY KEY (parent_id, child_id)
            );
            "#,
        )
        .unwrap();
        conn
    }

    #[test]
    fn test_atomic_claim_ready_success() {
        let tmp = NamedTempFile::new().unwrap();
        let conn = setup_test_kanban_schema(&tmp);

        conn.execute(
            "INSERT INTO tasks (id, title, assignee, status, created_at) VALUES (?1, ?2, ?3, ?4, ?5)",
            rusqlite::params!["task-1", "Task 1", "worker-a", "ready", 1000],
        ).unwrap();

        let res = atomic_claim_task(tmp.path(), "task-1", "claimer-alpha", 300, "ready").unwrap();
        assert!(res.ok);
        assert!(res.claimed);
        assert_eq!(res.task_id.as_deref(), Some("task-1"));
        assert_eq!(res.status.as_deref(), Some("running"));
        assert_eq!(res.claim_lock.as_deref(), Some("claimer-alpha"));
        assert!(res.run_id.is_some());

        // Verify SQLite state
        let (st, lock, current_run_id): (String, Option<String>, Option<i64>) = conn
            .query_row(
                "SELECT status, claim_lock, current_run_id FROM tasks WHERE id = 'task-1'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .unwrap();
        assert_eq!(st, "running");
        assert_eq!(lock.as_deref(), Some("claimer-alpha"));
        assert_eq!(current_run_id, res.run_id);

        // Verify task_runs
        let run_count: i64 = conn
            .query_row("SELECT count(*) FROM task_runs WHERE task_id = 'task-1'", [], |r| r.get(0))
            .unwrap();
        assert_eq!(run_count, 1);

        // Verify task_events
        let ev_kind: String = conn
            .query_row("SELECT kind FROM task_events WHERE task_id = 'task-1'", [], |r| r.get(0))
            .unwrap();
        assert_eq!(ev_kind, "claimed");
    }

    #[test]
    fn test_atomic_claim_concurrent_conflict() {
        let tmp = NamedTempFile::new().unwrap();
        let conn = setup_test_kanban_schema(&tmp);

        conn.execute(
            "INSERT INTO tasks (id, title, assignee, status, created_at) VALUES (?1, ?2, ?3, ?4, ?5)",
            rusqlite::params!["task-race", "Task Race", "worker-a", "ready", 1000],
        ).unwrap();

        // First claim wins
        let res1 = atomic_claim_task(tmp.path(), "task-race", "worker-1", 300, "ready").unwrap();
        assert!(res1.claimed);

        // Second claim loses CAS
        let res2 = atomic_claim_task(tmp.path(), "task-race", "worker-2", 300, "ready").unwrap();
        assert!(res2.ok);
        assert!(!res2.claimed);
        assert_eq!(res2.rejection_reason.as_deref(), Some("cas_conflict_or_invalid_status"));
    }

    #[test]
    fn test_atomic_claim_dependency_rejection() {
        let tmp = NamedTempFile::new().unwrap();
        let conn = setup_test_kanban_schema(&tmp);

        // Parent task in 'running' status (not done or archived)
        conn.execute(
            "INSERT INTO tasks (id, title, assignee, status, created_at) VALUES (?1, ?2, ?3, ?4, ?5)",
            rusqlite::params!["parent-1", "Parent", "worker-p", "running", 1000],
        ).unwrap();
        // Child task in 'ready' status
        conn.execute(
            "INSERT INTO tasks (id, title, assignee, status, created_at) VALUES (?1, ?2, ?3, ?4, ?5)",
            rusqlite::params!["child-1", "Child", "worker-c", "ready", 1010],
        ).unwrap();
        // Link parent to child
        conn.execute(
            "INSERT INTO task_links (parent_id, child_id) VALUES (?1, ?2)",
            rusqlite::params!["parent-1", "child-1"],
        ).unwrap();

        let res = atomic_claim_task(tmp.path(), "child-1", "claimer-x", 300, "ready").unwrap();
        assert!(res.ok);
        assert!(!res.claimed);
        assert_eq!(res.rejection_reason.as_deref(), Some("parents_not_done"));

        // Verify task demoted to 'todo'
        let st: String = conn
            .query_row("SELECT status FROM tasks WHERE id = 'child-1'", [], |r| r.get(0))
            .unwrap();
        assert_eq!(st, "todo");

        // Verify claim_rejected event was recorded
        let ev_kind: String = conn
            .query_row("SELECT kind FROM task_events WHERE task_id = 'child-1'", [], |r| r.get(0))
            .unwrap();
        assert_eq!(ev_kind, "claim_rejected");
    }

    #[test]
    fn test_heartbeat_claim_success_and_fencing() {
        let tmp = NamedTempFile::new().unwrap();
        let conn = setup_test_kanban_schema(&tmp);

        conn.execute(
            "INSERT INTO tasks (id, title, assignee, status, created_at) VALUES (?1, ?2, ?3, ?4, ?5)",
            rusqlite::params!["task-hb", "Heartbeat Task", "worker-a", "ready", 1000],
        ).unwrap();

        let claim_res = atomic_claim_task(tmp.path(), "task-hb", "owner-lock", 100, "ready").unwrap();
        assert!(claim_res.claimed);

        // Heartbeat from correct owner
        let hb_ok = heartbeat_claim_task(tmp.path(), "task-hb", "owner-lock", 500).unwrap();
        assert!(hb_ok.ok);
        assert!(hb_ok.renewed);

        // Heartbeat from imposter/stale owner fails
        let hb_fail = heartbeat_claim_task(tmp.path(), "task-hb", "imposter-lock", 500).unwrap();
        assert!(hb_fail.ok);
        assert!(!hb_fail.renewed);
    }
}