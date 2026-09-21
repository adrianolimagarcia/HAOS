use serde::Deserialize;
use serde_json::Value;
use std::fs::File;
use std::io::BufReader;
use std::path::Path;

#[derive(Deserialize, Debug)]
pub struct SessionQuery {
    pub session_id: String,
    pub messages: Option<String>,
    pub resolve_model: Option<String>,
    pub msg_limit: Option<usize>,
    pub msg_before: Option<usize>,
}

pub fn load_and_prepare_session(haos_home: &Path, query: &SessionQuery) -> Option<Value> {
    let sid = &query.session_id;
    if sid.is_empty() || sid.contains('/') || sid.contains('\\') || sid.contains("..") {
        return None;
    }

    let load_messages = query.messages.as_deref().unwrap_or("1") != "0";
    let session_file = haos_home.join("webui").join("sessions").join(format!("{sid}.json"));

    // O sidecar JSON do WebUI é o formato canônico completo da conversa
    let file = File::open(&session_file).ok()?;
    let reader = BufReader::new(file);
    let mut session_val: Value = serde_json::from_reader(reader).ok()?;

    if !session_val.is_object() {
        return None;
    }

    let sidecar_messages: Vec<Value> = session_val
        .get("messages")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let total_count = sidecar_messages.len();

    // Paginação e Windowing
    let (truncated_msgs, offset) = if !load_messages {
        (Vec::new(), 0)
    } else if let Some(limit) = query.msg_limit {
        let before = query.msg_before.unwrap_or(total_count);
        let end = before.min(total_count);
        let start = end.saturating_sub(limit);
        (sidecar_messages[start..end].to_vec(), start)
    } else {
        (sidecar_messages, 0)
    };

    let is_truncated = load_messages && query.msg_limit.is_some() && offset > 0;

    // Metadados essenciais para o frontend WebUI
    session_val["messages"] = Value::Array(truncated_msgs);
    session_val["message_count"] = Value::from(total_count);
    session_val["_messages_truncated"] = Value::Bool(is_truncated);
    session_val["_messages_offset"] = Value::from(offset);
    session_val["_msg_limit_max"] = Value::from(1000);
    session_val["is_streaming"] = Value::Bool(false);
    session_val["has_pending_user_message"] = Value::Bool(false);

    if session_val.get("last_message_at").is_none() {
        let last_at = session_val.get("updated_at").cloned().unwrap_or(Value::from(0));
        session_val["last_message_at"] = last_at;
    }

    if session_val.get("cache_hit_percent").is_none() {
        session_val["cache_hit_percent"] = Value::from(0);
    }

    if session_val.get("tool_calls").is_none() {
        session_val["tool_calls"] = Value::Array(Vec::new());
    }
    if session_val.get("pending_attachments").is_none() {
        session_val["pending_attachments"] = Value::Array(Vec::new());
    }
    if session_val.get("context_length").is_none() || session_val["context_length"] == 0 {
        session_val["context_length"] = Value::from(1_048_576);
    }

    Some(session_val)
}
