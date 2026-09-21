use rusqlite::{Connection, OpenFlags};
use serde::Deserialize;
use serde_json::Value;
use std::collections::HashSet;
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

    // 1. Tentar ler o sidecar JSON do WebUI
    let mut session_val: Value = if session_file.exists() {
        let file = File::open(&session_file).ok()?;
        let reader = BufReader::new(file);
        serde_json::from_reader(reader).ok()?
    } else {
        serde_json::json!({
            "session_id": sid,
            "title": "Sessão Agêntica",
            "messages": [],
            "message_count": 0,
            "context_length": 1_000_000,
        })
    };

    let mut sidecar_messages: Vec<Value> = session_val
        .get("messages")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    // 2. Mesclar mensagens do state.db se existirem
    let state_db_path = haos_home.join("state.db");
    if state_db_path.exists() {
        if let Ok(conn) = Connection::open_with_flags(
            &state_db_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        ) {
            // Verificar título ou metadados atualizados
            if let Ok(mut stmt) = conn.prepare(
                "SELECT title, model, cwd FROM sessions WHERE id = ?1 LIMIT 1;"
            ) {
                if let Ok(mut rows) = stmt.query([sid]) {
                    if let Ok(Some(row)) = rows.next() {
                        if let Ok(t) = row.get::<_, Option<String>>(0) {
                            if let Some(title_str) = t {
                                if !title_str.is_empty() {
                                    session_val["title"] = Value::String(title_str);
                                }
                            }
                        }
                        if let Ok(m) = row.get::<_, Option<String>>(1) {
                            if let Some(model_str) = m {
                                session_val["model"] = Value::String(model_str);
                            }
                        }
                        if let Ok(cwd) = row.get::<_, Option<String>>(2) {
                            if let Some(workspace_str) = cwd {
                                session_val["workspace"] = Value::String(workspace_str);
                            }
                        }
                    }
                }
            }

            // Ler mensagens do SQLite
            if load_messages {
                let msg_query = "SELECT role, content, tool_calls, timestamp FROM messages WHERE session_id = ?1 ORDER BY id ASC;";
                if let Ok(mut stmt) = conn.prepare(msg_query) {
                    if let Ok(msg_rows) = stmt.query_map([sid], |row| {
                        let role: String = row.get(0)?;
                        let content: String = row.get(1)?;
                        let tool_calls: Option<String> = row.get(2)?;
                        let timestamp: Option<f64> = row.get(3)?;
                        Ok((role, content, tool_calls, timestamp))
                    }) {
                        let mut known_keys = HashSet::new();
                        for m in &sidecar_messages {
                            let role = m.get("role").and_then(|v| v.as_str()).unwrap_or("");
                            let content = m.get("content").and_then(|v| v.as_str()).unwrap_or("");
                            known_keys.insert((role.to_string(), content.to_string()));
                        }

                        for r in msg_rows.flatten() {
                            let (role, content, tc_raw, ts) = r;
                            let key = (role.clone(), content.clone());
                            if !known_keys.contains(&key) {
                                known_keys.insert(key);
                                let mut msg_obj = serde_json::json!({
                                    "role": role,
                                    "content": content,
                                });
                                if let Some(t) = ts {
                                    msg_obj["timestamp"] = Value::from(t);
                                }
                                if let Some(tc_str) = tc_raw {
                                    if let Ok(parsed_tc) = serde_json::from_str::<Value>(&tc_str) {
                                        msg_obj["tool_calls"] = parsed_tc;
                                    }
                                }
                                sidecar_messages.push(msg_obj);
                            }
                        }
                    }
                }
            }
        }
    }

    let total_count = sidecar_messages.len();

    // 3. Paginação e Windowing
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

    session_val["messages"] = Value::Array(truncated_msgs);
    session_val["message_count"] = Value::from(total_count);
    session_val["_messages_truncated"] = Value::Bool(is_truncated);
    session_val["_messages_offset"] = Value::from(offset);
    session_val["_msg_limit_max"] = Value::from(1000);

    // Garantir campos essenciais esperados pelo frontend
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
