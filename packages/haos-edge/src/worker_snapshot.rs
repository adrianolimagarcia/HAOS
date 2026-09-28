//! Read-only worker supervision snapshots.
//!
//! The kanban database remains the authority for assignment/status/heartbeat/
//! ownership. This module only reads it and inspects `/proc`; it never kills,
//! reaps, renews, claims, or mutates a worker.
use rusqlite::{Connection, OpenFlags};
use serde::Serialize;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct WorkerSnapshot {
    pub task_id: String,
    pub pid: Option<i64>,
    pub assignee: Option<String>,
    pub status: String,
    pub heartbeat_at: Option<i64>,
    pub ownership: Option<String>,
    pub profile: Option<String>,
    pub process: ProcessObservation,
    pub heartbeat_lost: bool,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct ProcessObservation {
    pub state: &'static str,
    pub pid_reused: bool,
}

pub fn read_snapshots(
    db_path: &Path,
    profile: Option<&str>,
    now: i64,
    max_idle_seconds: i64,
) -> rusqlite::Result<Vec<WorkerSnapshot>> {
    let conn = Connection::open_with_flags(db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let columns = table_columns(&conn)?;
    let Some(id) = first_column(&columns, &["id", "task_id", "spec_id"]) else {
        return Ok(Vec::new());
    };
    let pid = first_column(&columns, &["worker_pid", "pid"]);
    let assignee = first_column(&columns, &["assignee", "claimer", "worker_id"]);
    let status = first_column(&columns, &["status"]);
    let heartbeat = first_column(&columns, &["last_heartbeat_at", "heartbeat_at"]);
    let ownership = first_column(&columns, &["claim_lock", "ownership", "claimer"]);
    let profile_col = first_column(&columns, &["profile", "profile_name", "hermes_profile"]);
    let start_col = first_column(
        &columns,
        &["worker_start_time", "worker_started_at", "pid_start_time"],
    );
    let sql = format!(
        "SELECT {id},{},{},{},{},{},{},{} FROM tasks{} ORDER BY {id}",
        pid.unwrap_or("NULL"),
        assignee.unwrap_or("NULL"),
        status.unwrap_or("NULL"),
        heartbeat.unwrap_or("NULL"),
        ownership.unwrap_or("NULL"),
        profile_col.unwrap_or("NULL"),
        start_col.unwrap_or("NULL"),
        profile_col
            .map(|c| format!(" WHERE {c} = ?1"))
            .unwrap_or_default()
    );
    let mut stmt = conn.prepare(&sql)?;
    let mut rows = match profile {
        Some(p) if profile_col.is_some() => stmt.query(rusqlite::params![p])?,
        _ => stmt.query([])?,
    };
    let mut out = Vec::new();
    while let Some(row) = rows.next()? {
        let heartbeat_at: Option<i64> = row.get(4)?;
        out.push(WorkerSnapshot {
            task_id: row.get(0)?,
            pid: row.get(1)?,
            assignee: row.get(2)?,
            status: row
                .get::<_, Option<String>>(3)?
                .unwrap_or_else(|| "unknown".into()),
            heartbeat_at,
            ownership: row.get(5)?,
            profile: row.get(6)?,
            process: observe_process(row.get(1)?, row.get(7)?),
            heartbeat_lost: heartbeat_at
                .map(|t| now.saturating_sub(t) > max_idle_seconds)
                .unwrap_or(true),
        });
    }
    Ok(out)
}

fn table_columns(conn: &Connection) -> rusqlite::Result<std::collections::HashSet<String>> {
    let mut stmt = conn.prepare("PRAGMA table_info(tasks)")?;
    let columns = stmt
        .query_map([], |r| r.get::<_, String>(1))?
        .filter_map(Result::ok)
        .collect();
    Ok(columns)
}
fn first_column<'a>(
    cols: &std::collections::HashSet<String>,
    names: &'a [&'a str],
) -> Option<&'a str> {
    names.iter().find(|n| cols.contains(**n)).copied()
}

fn observe_process(pid: Option<i64>, recorded_start: Option<String>) -> ProcessObservation {
    let Some(pid) = pid.filter(|p| *p > 1) else {
        return ProcessObservation {
            state: "absent",
            pid_reused: false,
        };
    };
    let Ok(stat) = std::fs::read_to_string(format!("/proc/{pid}/stat")) else {
        return ProcessObservation {
            state: "absent",
            pid_reused: false,
        };
    };
    let fields: Vec<&str> = stat
        .rsplit_once(") ")
        .map(|(_, rest)| rest.split_whitespace().collect())
        .unwrap_or_default();
    let reused = recorded_start
        .as_deref()
        .zip(fields.get(19).copied())
        .map(|(a, b)| a != b)
        .unwrap_or(false);
    ProcessObservation {
        state: if reused { "pid_reused" } else { "alive" },
        pid_reused: reused,
    }
}

pub fn unix_now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;
    fn db(sql: &str) -> std::path::PathBuf {
        let p = std::env::temp_dir().join(format!(
            "haos-worker-snapshot-{}-{}.db",
            std::process::id(),
            std::thread::current().name().unwrap_or("test")
        ));
        let _ = std::fs::remove_file(&p);
        let c = Connection::open(&p).unwrap();
        c.execute_batch(sql).unwrap();
        p
    }
    #[test]
    fn absent_pid_and_lost_heartbeat() {
        let p = db("CREATE TABLE tasks(id TEXT,status TEXT,worker_pid INTEGER,last_heartbeat_at INTEGER,assignee TEXT,claim_lock TEXT,profile TEXT); INSERT INTO tasks VALUES('t','RUNNING',999999,10,'a','o','p');");
        let s = read_snapshots(&p, Some("p"), 100, 30).unwrap();
        assert_eq!(s[0].process.state, "absent");
        assert!(s[0].heartbeat_lost);
    }
    #[test]
    fn profile_filter_is_read_only() {
        let p = db("CREATE TABLE tasks(id TEXT,status TEXT,profile TEXT); INSERT INTO tasks VALUES('a','RUNNING','A'); INSERT INTO tasks VALUES('b','RUNNING','B');");
        let s = read_snapshots(&p, Some("B"), 100, 30).unwrap();
        assert_eq!(s[0].task_id, "b");
    }
    #[test]
    fn pid_reuse_marker_is_conservative() {
        let current =
            std::fs::read_to_string(format!("/proc/{}/stat", std::process::id())).unwrap();
        let start = current
            .rsplit_once(") ")
            .unwrap()
            .1
            .split_whitespace()
            .nth(19)
            .unwrap();
        assert!(
            observe_process(Some(std::process::id() as i64), Some("different".into())).pid_reused
        );
        assert!(!observe_process(Some(std::process::id() as i64), Some(start.into())).pid_reused);
    }
}
