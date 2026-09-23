//! Pure validation for the typed Rust writer IPC v2 envelope.
//!
//! This module deliberately has no HTTP, SQLite, filesystem, or startup dependencies.  It
//! validates the wire contract and the immutable profile/data-directory binding only.

use serde_json::{Map, Value};
use std::fmt;
use std::path::{Component, Path};

pub const CONTRACT_VERSION: &str = "haos-edge.rust-writer.v2";
pub const SCHEMA_VERSION: i64 = 2;
pub const MIN_TIMEOUT_MS: i64 = 1;
pub const MAX_TIMEOUT_MS: i64 = 30_000;

const OPERATIONS: &[&str] = &[
    "create_session",
    "append_messages",
    "update_session",
    "set_session_archived",
    "set_session_hidden",
    "set_session_pinned",
    "set_session_read",
    "set_session_title",
    "update_session_cwd",
    "update_session_meta",
    "set_message_reaction",
    "delete_session",
    "archive_and_compact",
];

/// The immutable identity against which each request is checked.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WriterBinding {
    profile: String,
    data_dir: String,
}

impl WriterBinding {
    /// Build a binding without consulting the filesystem.
    pub fn new(
        profile: impl Into<String>,
        data_dir: impl AsRef<str>,
    ) -> Result<Self, ValidationError> {
        let profile = profile.into();
        validate_profile(&profile)?;
        let data_dir = normalize_data_dir(data_dir.as_ref())?;
        Ok(Self { profile, data_dir })
    }

    pub fn profile(&self) -> &str {
        &self.profile
    }

    pub fn data_dir(&self) -> &str {
        &self.data_dir
    }
}

/// A validated request.  `data_dir` is the normalized binding value.
#[derive(Debug, Clone, PartialEq)]
pub struct ValidatedEnvelope {
    pub contract_version: String,
    pub schema_version: i64,
    pub profile: String,
    pub data_dir: String,
    pub operation: String,
    pub idempotency_key: String,
    pub timeout_ms: i64,
    pub payload: Value,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ValidationError {
    InvalidRequest,
    UnsupportedContractVersion,
    UnsupportedSchemaVersion,
    UnknownOperation,
    MissingIdempotencyKey,
    InvalidIdempotencyKey,
    InvalidTimeout,
    ProfileMismatch,
    DataDirMismatch,
}

impl ValidationError {
    pub const fn code(self) -> &'static str {
        match self {
            Self::InvalidRequest => "invalid_request",
            Self::UnsupportedContractVersion => "unsupported_contract_version",
            Self::UnsupportedSchemaVersion => "unsupported_schema_version",
            Self::UnknownOperation => "unknown_operation",
            Self::MissingIdempotencyKey => "missing_idempotency_key",
            Self::InvalidIdempotencyKey => "invalid_idempotency_key",
            Self::InvalidTimeout => "invalid_timeout",
            Self::ProfileMismatch => "profile_mismatch",
            Self::DataDirMismatch => "data_dir_mismatch",
        }
    }
}

impl fmt::Display for ValidationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}

impl std::error::Error for ValidationError {}

/// Validate a JSON value against the v2 envelope and an immutable instance binding.
pub fn validate_envelope(
    envelope: &Value,
    binding: &WriterBinding,
) -> Result<ValidatedEnvelope, ValidationError> {
    let object = envelope
        .as_object()
        .ok_or(ValidationError::InvalidRequest)?;
    reject_unknown(
        object,
        &[
            "contract_version",
            "schema_version",
            "profile",
            "data_dir",
            "operation",
            "idempotency_key",
            "timeout_ms",
            "payload",
        ],
    )?;

    let contract_version = required_string(object, "contract_version")?;
    if contract_version != CONTRACT_VERSION {
        return Err(ValidationError::UnsupportedContractVersion);
    }

    let schema_version = object
        .get("schema_version")
        .and_then(Value::as_i64)
        .ok_or(ValidationError::InvalidRequest)?;
    if schema_version != SCHEMA_VERSION {
        return Err(ValidationError::UnsupportedSchemaVersion);
    }

    let profile = required_string(object, "profile")?;
    validate_profile(&profile)?;
    if profile != binding.profile {
        return Err(ValidationError::ProfileMismatch);
    }

    let data_dir_raw = required_string(object, "data_dir")?;
    let data_dir = normalize_data_dir(&data_dir_raw)?;
    if data_dir != binding.data_dir {
        return Err(ValidationError::DataDirMismatch);
    }

    let operation = required_string(object, "operation")?;
    if !OPERATIONS.contains(&operation.as_str()) {
        return Err(ValidationError::UnknownOperation);
    }

    let idempotency_key = match object.get("idempotency_key") {
        None => return Err(ValidationError::MissingIdempotencyKey),
        Some(value) => value
            .as_str()
            .ok_or(ValidationError::InvalidIdempotencyKey)?
            .to_owned(),
    };
    validate_idempotency_key(&idempotency_key)?;

    let timeout_ms = object
        .get("timeout_ms")
        .and_then(Value::as_i64)
        .ok_or(ValidationError::InvalidTimeout)?;
    if !(MIN_TIMEOUT_MS..=MAX_TIMEOUT_MS).contains(&timeout_ms) {
        return Err(ValidationError::InvalidTimeout);
    }

    let payload = object
        .get("payload")
        .ok_or(ValidationError::InvalidRequest)?
        .clone();
    validate_payload(&operation, &payload)?;

    Ok(ValidatedEnvelope {
        contract_version,
        schema_version,
        profile,
        data_dir,
        operation,
        idempotency_key,
        timeout_ms,
        payload,
    })
}

/// Parse JSON and then apply [`validate_envelope`].
pub fn parse_and_validate(
    json: &str,
    binding: &WriterBinding,
) -> Result<ValidatedEnvelope, ValidationError> {
    let value = serde_json::from_str(json).map_err(|_| ValidationError::InvalidRequest)?;
    validate_envelope(&value, binding)
}

/// Return the closed operation registry without exposing a mutable collection.
pub fn supported_operations() -> &'static [&'static str] {
    OPERATIONS
}

pub fn validate_idempotency_key(key: &str) -> Result<(), ValidationError> {
    if key.is_empty() || key.len() > 200 || !key.is_ascii() {
        return Err(ValidationError::InvalidIdempotencyKey);
    }
    let bytes = key.as_bytes();
    if !is_key_start(bytes[0]) || !bytes[1..].iter().all(|byte| is_key_rest(*byte)) {
        return Err(ValidationError::InvalidIdempotencyKey);
    }
    Ok(())
}

fn is_key_start(byte: u8) -> bool {
    byte.is_ascii_alphanumeric()
}

fn is_key_rest(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-')
}

fn required_string(object: &Map<String, Value>, name: &str) -> Result<String, ValidationError> {
    object
        .get(name)
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
        .ok_or(ValidationError::InvalidRequest)
}

fn reject_unknown(object: &Map<String, Value>, allowed: &[&str]) -> Result<(), ValidationError> {
    if object.keys().all(|key| allowed.contains(&key.as_str())) {
        Ok(())
    } else {
        Err(ValidationError::InvalidRequest)
    }
}

fn object_payload<'a>(payload: &'a Value) -> Result<&'a Map<String, Value>, ValidationError> {
    payload.as_object().ok_or(ValidationError::InvalidRequest)
}

fn validate_payload(operation: &str, payload: &Value) -> Result<(), ValidationError> {
    match operation {
        "create_session" => validate_object(
            payload,
            &[
                "session_id",
                "source",
                "title",
                "model",
                "cwd",
                "parent_session_id",
                "profile_name",
            ],
            &["session_id", "source"],
        )
        .map(|_| ()),
        "append_messages" => {
            let object = validate_object(
                payload,
                &["session_id", "messages"],
                &["session_id", "messages"],
            )?;
            let messages = object
                .get("messages")
                .ok_or(ValidationError::InvalidRequest)?;
            let messages = messages.as_array().ok_or(ValidationError::InvalidRequest)?;
            if messages.is_empty() {
                return Err(ValidationError::InvalidRequest);
            }
            for message in messages {
                let message = validate_object(
                    message,
                    &["role", "content", "api_content", "timestamp", "message_id"],
                    &["role", "content"],
                )?;
                string_field(message, "role")?;
                if !message.get("content").is_some_and(is_json_value) {
                    return Err(ValidationError::InvalidRequest);
                }
                optional_string_field(message, "api_content")?;
                if let Some(timestamp) = message.get("timestamp") {
                    if !timestamp.is_number() {
                        return Err(ValidationError::InvalidRequest);
                    }
                }
                optional_string_field(message, "message_id")?;
            }
            Ok(())
        }
        "update_session" => validate_object(
            payload,
            &[
                "session_id",
                "model",
                "provider",
                "cwd",
                "git_branch",
                "git_repo_root",
                "profile_name",
            ],
            &["session_id"],
        )
        .map(|_| ()),
        "set_session_archived"
        | "set_session_hidden"
        | "set_session_pinned"
        | "set_session_read" => {
            let field = match operation {
                "set_session_archived" => "archived",
                "set_session_hidden" => "hidden",
                "set_session_pinned" => "pinned",
                _ => "read",
            };
            let object = validate_object(payload, &["session_id", field], &["session_id"])?;
            bool_field(object, field)
        }
        "set_session_title" => {
            validate_object(payload, &["session_id", "title"], &["session_id", "title"]).map(|_| ())
        }
        "update_session_cwd" => validate_object(
            payload,
            &["session_id", "cwd", "git_branch", "git_repo_root"],
            &["session_id", "cwd"],
        )
        .map(|_| ()),
        "update_session_meta" => {
            let object = validate_object(
                payload,
                &["session_id", "model_config", "model"],
                &["session_id", "model_config"],
            )?;
            if !object.get("model_config").is_some_and(Value::is_object) {
                return Err(ValidationError::InvalidRequest);
            }
            optional_string_field(object, "model")
        }
        "set_message_reaction" => {
            let object = validate_object(
                payload,
                &["session_id", "message_row_id", "emoji"],
                &["session_id", "message_row_id"],
            )?;
            let row_id = object.get("message_row_id").and_then(Value::as_i64);
            if !row_id.is_some_and(|id| id > 0) {
                return Err(ValidationError::InvalidRequest);
            }
            if !object
                .get("emoji")
                .is_some_and(|value| value.is_null() || value.is_string())
            {
                return Err(ValidationError::InvalidRequest);
            }
            Ok(())
        }
        "delete_session" => {
            let object = validate_object(
                payload,
                &["session_id", "delete_transcript"],
                &["session_id"],
            )?;
            if let Some(value) = object.get("delete_transcript") {
                if !value.is_boolean() {
                    return Err(ValidationError::InvalidRequest);
                }
            }
            Ok(())
        }
        "archive_and_compact" => {
            let object = validate_object(
                payload,
                &["session_id", "messages", "summary", "reason"],
                &["session_id", "messages", "summary", "reason"],
            )?;
            let messages = object
                .get("messages")
                .and_then(Value::as_array)
                .ok_or(ValidationError::InvalidRequest)?;
            if messages.is_empty() {
                return Err(ValidationError::InvalidRequest);
            }
            for message in messages {
                let message = validate_object(
                    message,
                    &["role", "content", "api_content", "timestamp", "message_id"],
                    &["role", "content"],
                )?;
                string_field(message, "role")?;
                if !message.get("content").is_some_and(is_json_value) {
                    return Err(ValidationError::InvalidRequest);
                }
                optional_string_field(message, "api_content")?;
                optional_string_field(message, "message_id")?;
                if let Some(timestamp) = message.get("timestamp") {
                    if !timestamp.is_number() {
                        return Err(ValidationError::InvalidRequest);
                    }
                }
            }
            Ok(())
        }
        _ => Err(ValidationError::UnknownOperation),
    }
}

fn validate_object<'a>(
    payload: &'a Value,
    allowed: &[&str],
    required: &[&str],
) -> Result<&'a Map<String, Value>, ValidationError> {
    let object = object_payload(payload)?;
    reject_unknown(object, allowed)?;
    if required.iter().all(|name| object.contains_key(*name)) {
        for name in required {
            if *name != "messages" && *name != "model_config" && *name != "message_row_id" {
                string_field(object, name)?;
            }
        }
        Ok(object)
    } else {
        Err(ValidationError::InvalidRequest)
    }
}

fn string_field(object: &Map<String, Value>, name: &str) -> Result<(), ValidationError> {
    if object.get(name).is_some_and(Value::is_string) {
        Ok(())
    } else {
        Err(ValidationError::InvalidRequest)
    }
}

fn optional_string_field(object: &Map<String, Value>, name: &str) -> Result<(), ValidationError> {
    if object.get(name).is_none_or(Value::is_string) {
        Ok(())
    } else {
        Err(ValidationError::InvalidRequest)
    }
}

fn bool_field(object: &Map<String, Value>, name: &str) -> Result<(), ValidationError> {
    if object.get(name).is_some_and(Value::is_boolean) {
        Ok(())
    } else {
        Err(ValidationError::InvalidRequest)
    }
}

fn is_json_value(_: &Value) -> bool {
    true
}

fn validate_profile(profile: &str) -> Result<(), ValidationError> {
    if profile.is_empty()
        || profile == "."
        || profile == ".."
        || profile.contains('/')
        || profile.contains('\\')
        || profile.contains('\0')
    {
        return Err(ValidationError::InvalidRequest);
    }
    Ok(())
}

fn normalize_data_dir(data_dir: &str) -> Result<String, ValidationError> {
    if data_dir.is_empty() || data_dir.contains('\0') {
        return Err(ValidationError::InvalidRequest);
    }
    let path = Path::new(data_dir);
    if !path.is_absolute() {
        return Err(ValidationError::InvalidRequest);
    }

    let mut normalized = String::from("/");
    for component in path.components() {
        match component {
            Component::RootDir | Component::CurDir => {}
            Component::ParentDir => {
                if normalized != "/" {
                    normalized.truncate(normalized.rfind('/').unwrap_or(0));
                    if normalized.is_empty() {
                        normalized.push('/');
                    }
                }
            }
            Component::Normal(part) => {
                if normalized != "/" {
                    normalized.push('/');
                }
                normalized.push_str(part.to_str().ok_or(ValidationError::InvalidRequest)?);
            }
            Component::Prefix(_) => return Err(ValidationError::InvalidRequest),
        }
    }
    Ok(normalized)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn binding() -> WriterBinding {
        WriterBinding::new("default", "/var/lib/haos/../haos").unwrap()
    }

    fn valid(operation: &str, payload: Value) -> Value {
        json!({
            "contract_version": CONTRACT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "profile": "default",
            "data_dir": "/var/lib/haos",
            "operation": operation,
            "idempotency_key": "request:1",
            "timeout_ms": 2000,
            "payload": payload,
        })
    }

    #[test]
    fn accepts_typed_envelope_and_normalizes_binding_path() {
        let request = valid(
            "set_session_title",
            json!({"session_id": "s-1", "title": "new"}),
        );
        let validated = validate_envelope(&request, &binding()).unwrap();
        assert_eq!(validated.data_dir, "/var/lib/haos");
        assert_eq!(validated.operation, "set_session_title");
    }

    #[test]
    fn rejects_versions_timeout_and_idempotency_limits() {
        let mut request = valid("delete_session", json!({"session_id": "s-1"}));
        request["schema_version"] = json!(1);
        assert_eq!(
            validate_envelope(&request, &binding()),
            Err(ValidationError::UnsupportedSchemaVersion)
        );
        request["schema_version"] = json!(2);
        request["timeout_ms"] = json!(0);
        assert_eq!(
            validate_envelope(&request, &binding()),
            Err(ValidationError::InvalidTimeout)
        );
        request["timeout_ms"] = json!(1);
        request["idempotency_key"] = json!("bad key");
        assert_eq!(
            validate_envelope(&request, &binding()),
            Err(ValidationError::InvalidIdempotencyKey)
        );
    }

    #[test]
    fn rejects_profile_and_data_dir_mismatch() {
        let mut request = valid("delete_session", json!({"session_id": "s-1"}));
        request["profile"] = json!("other");
        assert_eq!(
            validate_envelope(&request, &binding()),
            Err(ValidationError::ProfileMismatch)
        );
        request["profile"] = json!("default");
        request["data_dir"] = json!("/srv/other");
        assert_eq!(
            validate_envelope(&request, &binding()),
            Err(ValidationError::DataDirMismatch)
        );
    }

    #[test]
    fn rejects_sql_escape_hatches_and_unknown_fields() {
        let request = valid("execute_sql", json!({"sql": "DELETE FROM sessions"}));
        assert_eq!(
            validate_envelope(&request, &binding()),
            Err(ValidationError::UnknownOperation)
        );
        let request = valid(
            "set_session_title",
            json!({"session_id": "s-1", "title": "x", "sql": "DROP TABLE"}),
        );
        assert_eq!(
            validate_envelope(&request, &binding()),
            Err(ValidationError::InvalidRequest)
        );
        let request = valid(
            "set_session_title",
            json!({"session_id": "s-1", "title": "x"}),
        );
        let mut request = request.as_object().unwrap().clone();
        request.insert("unknown".into(), json!(true));
        assert_eq!(
            validate_envelope(&Value::Object(request), &binding()),
            Err(ValidationError::InvalidRequest)
        );
    }

    #[test]
    fn validates_every_registered_operation_shape() {
        let payloads = [
            ("create_session", json!({"session_id":"s","source":"cli"})),
            (
                "append_messages",
                json!({"session_id":"s","messages":[{"role":"user","content":"hi"}]}),
            ),
            ("update_session", json!({"session_id":"s","model":"m"})),
            (
                "set_session_archived",
                json!({"session_id":"s","archived":true}),
            ),
            (
                "set_session_hidden",
                json!({"session_id":"s","hidden":false}),
            ),
            (
                "set_session_pinned",
                json!({"session_id":"s","pinned":true}),
            ),
            ("set_session_read", json!({"session_id":"s","read":true})),
            ("set_session_title", json!({"session_id":"s","title":"t"})),
            ("update_session_cwd", json!({"session_id":"s","cwd":"/tmp"})),
            (
                "update_session_meta",
                json!({"session_id":"s","model_config":{}}),
            ),
            (
                "set_message_reaction",
                json!({"session_id":"s","message_row_id":1,"emoji":null}),
            ),
            ("delete_session", json!({"session_id":"s"})),
            (
                "archive_and_compact",
                json!({"session_id":"s","messages":[{"role":"assistant","content":"x"}],"summary":"s","reason":"r"}),
            ),
        ];
        for (operation, payload) in payloads {
            assert!(
                validate_envelope(&valid(operation, payload), &binding()).is_ok(),
                "{operation}"
            );
        }
    }

    #[test]
    fn rejects_bad_typed_payloads_and_empty_message_lists() {
        let request = valid(
            "set_session_archived",
            json!({"session_id":"s","archived":"yes"}),
        );
        assert_eq!(
            validate_envelope(&request, &binding()),
            Err(ValidationError::InvalidRequest)
        );
        let request = valid("append_messages", json!({"session_id":"s","messages":[]}));
        assert_eq!(
            validate_envelope(&request, &binding()),
            Err(ValidationError::InvalidRequest)
        );
        let request = valid(
            "set_message_reaction",
            json!({"session_id":"s","message_row_id":0,"emoji":"👍"}),
        );
        assert_eq!(
            validate_envelope(&request, &binding()),
            Err(ValidationError::InvalidRequest)
        );
    }
}
