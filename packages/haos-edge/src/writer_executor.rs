//! Typed, single-writer executor for the SQLite state database.
//!
//! This first slice intentionally implements the session/message mutations that map
//! directly to the frozen `state-db-schema.sql`: `create_session`, `append_messages`,
//! the session flag/title/cwd/meta updates, and `delete_session`.  The remaining
//! envelope operations are rejected by [`WriterError::UnsupportedOperation`] until
//! their schema contract is frozen (reactions and archive/compact need extra durable
//! semantics).  There is no SQL in the request: every statement below is fixed and
//! parameterized.

use crate::writer_envelope::{ValidatedEnvelope, WriterBinding};
use crate::writer_lock::WriterLock;
use rusqlite::{params, Connection, Error as SqliteError, ErrorCode, Transaction};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const IDEMPOTENCY_SCHEMA: &str = r#"
CREATE TABLE IF NOT EXISTS rust_writer_idempotency (
    profile TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (profile, idempotency_key)
);
"#;

#[derive(Debug, Clone, PartialEq)]
pub struct WriterResult {
    pub operation: String,
    pub idempotency_key: String,
    pub result: Value,
}

#[derive(Debug)]
pub enum WriterError {
    InvalidBinding,
    Storage(String),
    SchemaMismatch,
    Timeout,
    Busy,
    NotFound,
    Conflict(String),
    IdempotencyConflict,
    InvalidPayload,
    UnsupportedOperation(String),
}

impl std::fmt::Display for WriterError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidBinding => f.write_str("invalid_binding"),
            Self::Storage(_) => f.write_str("storage_unavailable"),
            Self::SchemaMismatch => f.write_str("schema_mismatch"),
            Self::Timeout => f.write_str("timeout"),
            Self::Busy => f.write_str("busy"),
            Self::NotFound => f.write_str("not_found"),
            Self::Conflict(_) => f.write_str("conflict"),
            Self::IdempotencyConflict => f.write_str("idempotency_conflict"),
            Self::InvalidPayload => f.write_str("invalid_request"),
            Self::UnsupportedOperation(_) => f.write_str("unsupported_operation"),
        }
    }
}

impl std::error::Error for WriterError {}

/// Owns the logical writer lock and executes typed operations against one state.db.
pub struct WriterExecutor {
    data_dir: PathBuf,
    profile: String,
    db_path: PathBuf,
    _lock: WriterLock,
}

impl WriterExecutor {
    /// Acquire the per-profile lock.  No production database is opened until this succeeds.
    pub fn open(
        data_dir: impl AsRef<Path>,
        profile: impl Into<String>,
    ) -> Result<Self, WriterError> {
        let data_dir = data_dir.as_ref().to_path_buf();
        let profile = profile.into();
        let binding = WriterBinding::new(profile.clone(), data_dir.to_string_lossy())
            .map_err(|_| WriterError::InvalidBinding)?;
        let lock = WriterLock::acquire(&data_dir, binding.profile()).map_err(|e| {
            if e.kind() == std::io::ErrorKind::AlreadyExists {
                WriterError::Busy
            } else {
                WriterError::Storage(e.to_string())
            }
        })?;
        let db_path = data_dir.join("state.db");
        Ok(Self {
            data_dir,
            profile,
            db_path,
            _lock: lock,
        })
    }

    /// Open an executor using a supplied fingerprint, primarily for deterministic fixtures.
    #[cfg(test)]
    fn open_with_fingerprint(
        data_dir: impl AsRef<Path>,
        profile: &str,
    ) -> Result<Self, WriterError> {
        let data_dir = data_dir.as_ref().to_path_buf();
        let lock =
            WriterLock::acquire_with_fingerprint(&data_dir, profile, "test").map_err(|e| {
                if e.kind() == std::io::ErrorKind::AlreadyExists {
                    WriterError::Busy
                } else {
                    WriterError::Storage(e.to_string())
                }
            })?;
        Ok(Self {
            db_path: data_dir.join("state.db"),
            data_dir,
            profile: profile.to_owned(),
            _lock: lock,
        })
    }

    pub fn execute(&self, request: &ValidatedEnvelope) -> Result<WriterResult, WriterError> {
        self.execute_inner(request, |_| Ok(()))
    }

    // The hook is deliberately private to the crate.  It makes deadline/rollback tests
    // deterministic without adding a delay option to the wire contract.
    fn execute_inner<F>(
        &self,
        request: &ValidatedEnvelope,
        before_commit: F,
    ) -> Result<WriterResult, WriterError>
    where
        F: FnOnce(&Transaction<'_>) -> Result<(), WriterError>,
    {
        if request.profile != self.profile || request.data_dir != normalize_path(&self.data_dir) {
            return Err(WriterError::InvalidBinding);
        }
        let deadline = Instant::now() + Duration::from_millis(request.timeout_ms as u64);
        let fingerprint = request_fingerprint(request)?;
        let mut conn = Connection::open(&self.db_path).map_err(storage_error)?;
        conn.busy_timeout(Duration::from_millis(request.timeout_ms as u64))
            .map_err(storage_error)?;
        ensure_idempotency_table(&conn)?;
        check_deadline(deadline)?;
        let tx = conn.transaction().map_err(map_sql_error)?;
        tx.busy_timeout(Duration::from_millis(request.timeout_ms as u64))
            .map_err(storage_error)?;

        if let Some((stored_fingerprint, response)) =
            load_idempotency(&tx, &self.profile, &request.idempotency_key)?
        {
            if stored_fingerprint != fingerprint {
                return Err(WriterError::IdempotencyConflict);
            }
            return parse_stored_response(response);
        }

        let result = execute_operation(&tx, request, deadline)?;
        check_deadline(deadline)?;
        before_commit(&tx)?;
        check_deadline(deadline)?;
        let output = WriterResult {
            operation: request.operation.clone(),
            idempotency_key: request.idempotency_key.clone(),
            result,
        };
        let response_json = serde_json::to_string(&output.result)
            .map_err(|e| WriterError::Storage(e.to_string()))?;
        tx.execute(
            "INSERT INTO rust_writer_idempotency (profile, idempotency_key, fingerprint, response_json, created_at) VALUES (?1, ?2, ?3, ?4, ?5)",
            params![self.profile, request.idempotency_key, fingerprint, response_json, now_secs()],
        ).map_err(map_sql_error)?;
        check_deadline(deadline)?;
        tx.commit().map_err(map_sql_error)?;
        Ok(output)
    }
}

fn ensure_idempotency_table(conn: &Connection) -> Result<(), WriterError> {
    conn.execute_batch(IDEMPOTENCY_SCHEMA)
        .map_err(|e| match e {
            SqliteError::SqliteFailure(ref err, _) if err.code == ErrorCode::ReadOnly => {
                WriterError::SchemaMismatch
            }
            other => WriterError::Storage(other.to_string()),
        })?;
    Ok(())
}

fn execute_operation(
    tx: &Transaction<'_>,
    request: &ValidatedEnvelope,
    deadline: Instant,
) -> Result<Value, WriterError> {
    let payload = request
        .payload
        .as_object()
        .ok_or(WriterError::InvalidPayload)?;
    match request.operation.as_str() {
        "create_session" => create_session(tx, payload, deadline),
        "append_messages" => append_messages(tx, payload, deadline),
        "set_session_title" => update_one_string(tx, payload, "title", "title"),
        "set_session_archived" => update_one_bool(tx, payload, "archived", "archived"),
        "set_session_hidden" => update_one_bool(tx, payload, "hidden", "hidden"),
        "set_session_pinned" => update_one_bool(tx, payload, "pinned", "pinned"),
        "set_session_read" => set_session_read(tx, payload),
        "update_session_cwd" => update_cwd(tx, payload),
        "update_session" => update_session(tx, payload),
        "update_session_meta" => update_meta(tx, payload),
        "delete_session" => delete_session(tx, payload),
        operation => Err(WriterError::UnsupportedOperation(operation.to_owned())),
    }
}

fn create_session(
    tx: &Transaction<'_>,
    p: &Map<String, Value>,
    deadline: Instant,
) -> Result<Value, WriterError> {
    let id = string(p, "session_id")?;
    let source = string(p, "source")?;
    let changed = tx.execute(
        "INSERT INTO sessions (id, source, title, model, cwd, parent_session_id, profile_name, started_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        params![id, source, optional_string(p, "title"), optional_string(p, "model"), optional_string(p, "cwd"), optional_string(p, "parent_session_id"), optional_string(p, "profile_name"), now_secs()],
    ).map_err(map_sql_error)?;
    check_deadline(deadline)?;
    Ok(serde_json::json!({"session_id": id, "changed": changed == 1}))
}

fn append_messages(
    tx: &Transaction<'_>,
    p: &Map<String, Value>,
    deadline: Instant,
) -> Result<Value, WriterError> {
    let session_id = string(p, "session_id")?;
    if !exists(
        tx,
        "SELECT 1 FROM sessions WHERE id = ?1",
        params![session_id.clone()],
    )? {
        return Err(WriterError::NotFound);
    }
    let messages = p
        .get("messages")
        .and_then(Value::as_array)
        .ok_or(WriterError::InvalidPayload)?;
    let mut inserted = 0usize;
    for message in messages {
        let m = message.as_object().ok_or(WriterError::InvalidPayload)?;
        let role = string(m, "role")?;
        let content = m
            .get("content")
            .ok_or(WriterError::InvalidPayload)?
            .to_string();
        let timestamp = m
            .get("timestamp")
            .and_then(Value::as_f64)
            .unwrap_or_else(now_secs);
        tx.execute(
            "INSERT INTO messages (session_id, role, content, api_content, timestamp, platform_message_id) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![session_id, role, content, optional_string(m, "api_content"), timestamp, optional_string(m, "message_id")],
        ).map_err(map_sql_error)?;
        inserted += 1;
        check_deadline(deadline)?;
    }
    tx.execute("UPDATE sessions SET message_count = COALESCE(message_count, 0) + ?1, last_activity_at = ?2 WHERE id = ?3", params![inserted as i64, now_secs(), session_id.clone()]).map_err(map_sql_error)?;
    Ok(serde_json::json!({"session_id": session_id, "inserted": inserted}))
}

fn update_one_string(
    tx: &Transaction<'_>,
    p: &Map<String, Value>,
    field: &str,
    output: &str,
) -> Result<Value, WriterError> {
    let id = string(p, "session_id")?;
    let value = string(p, field)?;
    let sql = match field {
        "title" => "UPDATE sessions SET title = ?1 WHERE id = ?2",
        _ => return Err(WriterError::InvalidPayload),
    };
    let changed = tx
        .execute(sql, params![value, id.clone()])
        .map_err(map_sql_error)?;
    if changed == 0 {
        return Err(WriterError::NotFound);
    }
    Ok(serde_json::json!({"session_id": id, "field": output, "changed": true}))
}

fn update_one_bool(
    tx: &Transaction<'_>,
    p: &Map<String, Value>,
    field: &str,
    output: &str,
) -> Result<Value, WriterError> {
    let id = string(p, "session_id")?;
    let value = p
        .get(field)
        .and_then(Value::as_bool)
        .ok_or(WriterError::InvalidPayload)?;
    let sql = match field {
        "archived" => "UPDATE sessions SET archived = ?1 WHERE id = ?2",
        "hidden" => "UPDATE sessions SET hidden = ?1 WHERE id = ?2",
        "pinned" => "UPDATE sessions SET pinned = ?1 WHERE id = ?2",
        _ => return Err(WriterError::InvalidPayload),
    };
    let changed = tx
        .execute(sql, params![value as i64, id.clone()])
        .map_err(map_sql_error)?;
    if changed == 0 {
        return Err(WriterError::NotFound);
    }
    Ok(serde_json::json!({"session_id": id, output: value, "changed": true}))
}

fn set_session_read(tx: &Transaction<'_>, p: &Map<String, Value>) -> Result<Value, WriterError> {
    let id = string(p, "session_id")?;
    let read = p
        .get("read")
        .and_then(Value::as_bool)
        .ok_or(WriterError::InvalidPayload)?;
    let changed = tx
        .execute(
            "UPDATE sessions SET last_read_at = CASE WHEN ?1 THEN ?2 ELSE NULL END WHERE id = ?3",
            params![read, now_secs(), id.clone()],
        )
        .map_err(map_sql_error)?;
    if changed == 0 {
        return Err(WriterError::NotFound);
    }
    Ok(serde_json::json!({"session_id": id, "read": read, "changed": true}))
}

fn update_cwd(tx: &Transaction<'_>, p: &Map<String, Value>) -> Result<Value, WriterError> {
    let id = string(p, "session_id")?;
    let cwd = string(p, "cwd")?;
    let changed = tx.execute("UPDATE sessions SET cwd = ?1, git_branch = COALESCE(?2, git_branch), git_repo_root = COALESCE(?3, git_repo_root) WHERE id = ?4", params![cwd, optional_string(p, "git_branch"), optional_string(p, "git_repo_root"), id.clone()]).map_err(map_sql_error)?;
    if changed == 0 {
        return Err(WriterError::NotFound);
    }
    Ok(serde_json::json!({"session_id": id, "changed": true}))
}

fn update_session(tx: &Transaction<'_>, p: &Map<String, Value>) -> Result<Value, WriterError> {
    let id = string(p, "session_id")?;
    if !exists(
        tx,
        "SELECT 1 FROM sessions WHERE id = ?1",
        params![id.clone()],
    )? {
        return Err(WriterError::NotFound);
    }
    if p.contains_key("model") || p.contains_key("cwd") || p.contains_key("profile_name") {
        tx.execute("UPDATE sessions SET model = COALESCE(?1, model), cwd = COALESCE(?2, cwd), profile_name = COALESCE(?3, profile_name), git_branch = COALESCE(?4, git_branch), git_repo_root = COALESCE(?5, git_repo_root) WHERE id = ?6", params![optional_string(p, "model"), optional_string(p, "cwd"), optional_string(p, "profile_name"), optional_string(p, "git_branch"), optional_string(p, "git_repo_root"), id.clone()]).map_err(map_sql_error)?;
    }
    if let Some(provider) = p.get("provider") {
        let provider = provider.as_str().ok_or(WriterError::InvalidPayload)?;
        tx.execute(
            "UPDATE sessions SET billing_provider = ?1 WHERE id = ?2",
            params![provider, id.clone()],
        )
        .map_err(map_sql_error)?;
    }
    Ok(serde_json::json!({"session_id": id, "changed": true}))
}

fn update_meta(tx: &Transaction<'_>, p: &Map<String, Value>) -> Result<Value, WriterError> {
    let id = string(p, "session_id")?;
    let config = p
        .get("model_config")
        .filter(|value| value.is_object())
        .ok_or(WriterError::InvalidPayload)?
        .to_string();
    let changed = tx
        .execute(
            "UPDATE sessions SET model_config = ?1, model = COALESCE(?2, model) WHERE id = ?3",
            params![config, optional_string(p, "model"), id.clone()],
        )
        .map_err(map_sql_error)?;
    if changed == 0 {
        return Err(WriterError::NotFound);
    }
    Ok(serde_json::json!({"session_id": id, "changed": true}))
}

fn delete_session(tx: &Transaction<'_>, p: &Map<String, Value>) -> Result<Value, WriterError> {
    let id = string(p, "session_id")?;
    let delete_transcript = p
        .get("delete_transcript")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if delete_transcript {
        tx.execute(
            "DELETE FROM messages WHERE session_id = ?1",
            params![id.clone()],
        )
        .map_err(map_sql_error)?;
    }
    let changed = tx
        .execute("DELETE FROM sessions WHERE id = ?1", params![id.clone()])
        .map_err(map_sql_error)?;
    if changed == 0 {
        return Err(WriterError::NotFound);
    }
    Ok(
        serde_json::json!({"session_id": id, "deleted": true, "delete_transcript": delete_transcript}),
    )
}

fn load_idempotency(
    tx: &Transaction<'_>,
    profile: &str,
    key: &str,
) -> Result<Option<(String, String)>, WriterError> {
    let mut stmt = tx.prepare("SELECT fingerprint, response_json FROM rust_writer_idempotency WHERE profile = ?1 AND idempotency_key = ?2").map_err(map_sql_error)?;
    match stmt.query_row(params![profile, key], |row| Ok((row.get(0)?, row.get(1)?))) {
        Ok(value) => Ok(Some(value)),
        Err(SqliteError::QueryReturnedNoRows) => Ok(None),
        Err(error) => Err(map_sql_error(error)),
    }
}

fn parse_stored_response(response: String) -> Result<WriterResult, WriterError> {
    let result: Value =
        serde_json::from_str(&response).map_err(|e| WriterError::Storage(e.to_string()))?;
    Ok(WriterResult {
        operation: String::new(),
        idempotency_key: String::new(),
        result,
    })
}

fn request_fingerprint(request: &ValidatedEnvelope) -> Result<String, WriterError> {
    let canonical = serde_json::json!({"contract_version": request.contract_version, "schema_version": request.schema_version, "operation": request.operation, "profile": request.profile, "data_dir": request.data_dir, "payload": canonicalize(&request.payload)});
    let bytes = serde_json::to_vec(&canonical).map_err(|e| WriterError::Storage(e.to_string()))?;
    Ok(format!("sha256:{:x}", Sha256::digest(bytes)))
}

fn canonicalize(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut sorted = Map::new();
            let mut keys: Vec<_> = map.keys().collect();
            keys.sort();
            for key in keys {
                sorted.insert(key.clone(), canonicalize(&map[key]));
            }
            Value::Object(sorted)
        }
        Value::Array(values) => Value::Array(values.iter().map(canonicalize).collect()),
        other => other.clone(),
    }
}

fn exists<P: rusqlite::Params>(
    tx: &Transaction<'_>,
    sql: &str,
    params: P,
) -> Result<bool, WriterError> {
    tx.query_row(sql, params, |row| row.get::<_, i64>(0))
        .map(|_| true)
        .or_else(|e| match e {
            SqliteError::QueryReturnedNoRows => Ok(false),
            other => Err(map_sql_error(other)),
        })
}

fn string(map: &Map<String, Value>, name: &str) -> Result<String, WriterError> {
    map.get(name)
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
        .ok_or(WriterError::InvalidPayload)
}
fn optional_string(map: &Map<String, Value>, name: &str) -> Option<String> {
    map.get(name).and_then(Value::as_str).map(ToOwned::to_owned)
}
fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}
fn normalize_path(path: &Path) -> String {
    path.components()
        .collect::<PathBuf>()
        .to_string_lossy()
        .trim_end_matches('/')
        .to_owned()
}
fn check_deadline(deadline: Instant) -> Result<(), WriterError> {
    if Instant::now() >= deadline {
        Err(WriterError::Timeout)
    } else {
        Ok(())
    }
}

fn storage_error(error: rusqlite::Error) -> WriterError {
    map_sql_error(error)
}
fn map_sql_error(error: rusqlite::Error) -> WriterError {
    match error {
        SqliteError::SqliteFailure(ref err, _)
            if err.code == ErrorCode::DatabaseBusy || err.code == ErrorCode::DatabaseLocked =>
        {
            WriterError::Timeout
        }
        SqliteError::QueryReturnedNoRows => WriterError::NotFound,
        other => WriterError::Storage(other.to_string()),
    }
}

impl serde::Serialize for WriterResult {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeStruct;
        let mut state = serializer.serialize_struct("WriterResult", 3)?;
        state.serialize_field("operation", &self.operation)?;
        state.serialize_field("idempotency_key", &self.idempotency_key)?;
        state.serialize_field("result", &self.result)?;
        state.end()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;
    use serde_json::json;
    use tempfile::tempdir;

    fn fixture() -> (tempfile::TempDir, WriterExecutor) {
        let dir = tempdir().unwrap();
        let conn = Connection::open(dir.path().join("state.db")).unwrap();
        conn.execute_batch(include_str!("../../../docs/haos/state-db-schema.sql"))
            .unwrap();
        conn.execute_batch(IDEMPOTENCY_SCHEMA).unwrap();
        drop(conn);
        let executor = WriterExecutor::open_with_fingerprint(dir.path(), "default").unwrap();
        (dir, executor)
    }

    fn request(operation: &str, key: &str, payload: Value) -> ValidatedEnvelope {
        ValidatedEnvelope {
            contract_version: crate::writer_envelope::CONTRACT_VERSION.into(),
            schema_version: 2,
            profile: "default".into(),
            data_dir: String::new(),
            operation: operation.into(),
            idempotency_key: key.into(),
            timeout_ms: 2000,
            payload,
        }
    }

    fn bind_request(dir: &Path, mut request: ValidatedEnvelope) -> ValidatedEnvelope {
        request.data_dir = normalize_path(dir);
        request
    }

    #[test]
    fn transaction_persists_create_and_append_and_readback() {
        let (dir, executor) = fixture();
        let create = bind_request(
            dir.path(),
            request(
                "create_session",
                "create:1",
                json!({"session_id":"s1","source":"test","title":"T"}),
            ),
        );
        assert_eq!(executor.execute(&create).unwrap().result["changed"], true);
        let append = bind_request(
            dir.path(),
            request(
                "append_messages",
                "append:1",
                json!({"session_id":"s1","messages":[{"role":"user","content":"hello","timestamp":1.0}]}),
            ),
        );
        assert_eq!(executor.execute(&append).unwrap().result["inserted"], 1);
        let conn = Connection::open(dir.path().join("state.db")).unwrap();
        assert_eq!(
            conn.query_row(
                "SELECT title, message_count FROM sessions WHERE id='s1'",
                [],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))
            )
            .unwrap(),
            ("T".into(), 1)
        );
        assert_eq!(
            conn.query_row(
                "SELECT content FROM messages WHERE session_id='s1'",
                [],
                |r| r.get::<_, String>(0)
            )
            .unwrap(),
            "\"hello\""
        );
    }

    #[test]
    fn constraint_error_rolls_back_prior_mutations() {
        let (dir, executor) = fixture();
        let create = bind_request(
            dir.path(),
            request(
                "create_session",
                "create:2",
                json!({"session_id":"s2","source":"test"}),
            ),
        );
        executor.execute(&create).unwrap();
        let bad = bind_request(
            dir.path(),
            request(
                "append_messages",
                "append:bad",
                json!({"session_id":"missing","messages":[{"role":"user","content":"x"}]}),
            ),
        );
        assert!(matches!(executor.execute(&bad), Err(WriterError::NotFound)));
        let conn = Connection::open(dir.path().join("state.db")).unwrap();
        assert_eq!(
            conn.query_row("SELECT COUNT(*) FROM messages", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            0
        );
    }

    #[test]
    fn retry_is_idempotent_and_conflicting_payload_is_rejected() {
        let (dir, executor) = fixture();
        let first = bind_request(
            dir.path(),
            request(
                "create_session",
                "same:1",
                json!({"session_id":"s3","source":"test"}),
            ),
        );
        let first_result = executor.execute(&first).unwrap();
        let retry = executor.execute(&first).unwrap();
        assert_eq!(first_result.result, retry.result);
        let conflict = bind_request(
            dir.path(),
            request(
                "create_session",
                "same:1",
                json!({"session_id":"other","source":"test"}),
            ),
        );
        assert!(matches!(
            executor.execute(&conflict),
            Err(WriterError::IdempotencyConflict)
        ));
        let conn = Connection::open(dir.path().join("state.db")).unwrap();
        assert_eq!(
            conn.query_row("SELECT COUNT(*) FROM sessions", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            1
        );
    }

    #[test]
    fn locked_database_hits_timeout_and_leaves_no_write() {
        let (dir, executor) = fixture();
        let blocker = Connection::open(dir.path().join("state.db")).unwrap();
        blocker.execute_batch("BEGIN IMMEDIATE").unwrap();
        let request = bind_request(
            dir.path(),
            request(
                "create_session",
                "timeout:1",
                json!({"session_id":"st","source":"test"}),
            ),
        );
        let mut request = request;
        request.timeout_ms = 1;
        assert!(matches!(
            executor.execute(&request),
            Err(WriterError::Timeout)
        ));
        blocker.execute_batch("ROLLBACK").unwrap();
        let conn = Connection::open(dir.path().join("state.db")).unwrap();
        assert_eq!(
            conn.query_row("SELECT COUNT(*) FROM sessions WHERE id='st'", [], |r| r
                .get::<_, i64>(0))
                .unwrap(),
            0
        );
    }
}
