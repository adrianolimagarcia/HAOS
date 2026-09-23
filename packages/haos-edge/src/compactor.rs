//! Módulo de Compactação e Serialização Ultra-Rápida de Contexto (<1ms).
//!
//! Preserva estritamente:
//! 1. Mensagens de sistema no início (Prompt Caching imutável do LLM).
//! 2. Últimas N mensagens da conversa (Contexto imediato intacto).
//! 3. Trunca corpos volumosos de `tool` intermediários mantendo delimitadores válidos.
//! 4. Alternância estrita de papéis e integridade estrutural JSON.

use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use std::fs::{self, File};
use std::io::Write;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct ChatMessage {
    pub role: String,
    #[serde(default)]
    pub content: Option<serde_json::Value>,
    #[serde(default)]
    pub tool_calls: Option<serde_json::Value>,
    #[serde(default)]
    pub tool_call_id: Option<String>,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub reasoning: Option<String>,
    #[serde(flatten)]
    pub extra: serde_json::Map<String, serde_json::Value>,
}

#[derive(Deserialize)]
pub struct CompactPayload {
    pub messages: Vec<ChatMessage>,
    pub max_tokens: Option<usize>,
    pub max_tool_chars: Option<usize>,
    pub keep_last: Option<usize>,
}

#[derive(Serialize)]
pub struct CompactResult {
    pub ok: bool,
    pub original_count: usize,
    pub compacted_count: usize,
    pub truncated_tools: usize,
    pub estimated_tokens_saved: usize,
    pub messages: Vec<ChatMessage>,
}

pub struct ContextCompactor;

impl ContextCompactor {
    /// Compacta o vetor de mensagens preservando cabeçalho de caching e cauda viva.
    pub fn compact_messages(
        messages: Vec<ChatMessage>,
        max_tool_chars: usize,
        keep_last: usize,
    ) -> (Vec<ChatMessage>, usize, usize) {
        let original_count = messages.len();

        if original_count <= keep_last + 2 {
            return (messages, 0, 0);
        }

        let mut truncated_tools = 0;
        let mut tokens_saved = 0;
        let mut out = Vec::with_capacity(original_count);

        let system_count = messages.iter().take_while(|m| m.role == "system").count();
        let middle_start = system_count;
        let middle_end = original_count.saturating_sub(keep_last).max(middle_start);

        for (i, msg) in messages.into_iter().enumerate() {
            if i < middle_start || i >= middle_end {
                // Preserva cabeçalho do sistema e cauda intactos (Prompt Cache intacto)
                out.push(msg);
            } else {
                // Zona intermediária sujeita a compressão / truncamento
                let mut m = msg;
                if m.role == "tool" {
                    if let Some(serde_json::Value::String(ref s)) = m.content {
                        if s.len() > max_tool_chars {
                            let original_len = s.len();
                            let half = max_tool_chars / 2;
                            // Encontra fronteiras UTF-8 seguras próximas de half
                            let head_idx = s
                                .char_indices()
                                .map(|(idx, _)| idx)
                                .take_while(|&idx| idx <= half)
                                .last()
                                .unwrap_or(0);

                            let tail_target = original_len.saturating_sub(half);
                            let tail_idx = s
                                .char_indices()
                                .map(|(idx, _)| idx)
                                .find(|&idx| idx >= tail_target)
                                .unwrap_or(original_len);

                            let head = &s[..head_idx];
                            let tail = &s[tail_idx..];
                            let truncated = format!(
                                "{head}\n\n[... {} caracteres truncados pelo HAOS Rust Native Compactor ...]\n\n{tail}",
                                original_len.saturating_sub(head.len() + tail.len())
                            );
                            tokens_saved += original_len.saturating_sub(truncated.len()) / 4;
                            m.content = Some(serde_json::Value::String(truncated));
                            truncated_tools += 1;
                        }
                    }
                }
                out.push(m);
            }
        }

        (out, truncated_tools, tokens_saved)
    }

    /// Compacta o payload mantendo compatibilidade com haos-edge
    pub fn compact(payload: CompactPayload) -> CompactResult {
        let original_count = payload.messages.len();
        let max_tool_chars = payload.max_tool_chars.unwrap_or(2000);
        let keep_last = payload.keep_last.unwrap_or(6);

        let (out, truncated_tools, tokens_saved) =
            Self::compact_messages(payload.messages, max_tool_chars, keep_last);

        let compacted_count = out.len();
        CompactResult {
            ok: true,
            original_count,
            compacted_count,
            truncated_tools,
            estimated_tokens_saved: tokens_saved,
            messages: out,
        }
    }
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct WebUiSessionShrinkResult {
    pub session_id: String,
    pub title: Option<String>,
    pub size_bytes_before: u64,
    pub size_bytes_after: u64,
    pub messages_before: usize,
    pub messages_after: usize,
    pub messages_removed: usize,
    pub db_rows_deleted: usize,
    pub backup_path: Option<String>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct SessionVacuumReport {
    pub db_path: String,
    pub bytes_before: u64,
    pub bytes_after: u64,
    pub bytes_saved: i64,
    pub page_count_before: i64,
    pub page_count_after: i64,
    pub freelist_before: i64,
    pub freelist_after: i64,
    pub duration_ms: u128,
    pub success: bool,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct AutoMaintenanceReport {
    pub shrunk_sessions: Vec<WebUiSessionShrinkResult>,
    pub candidate_sessions_count: usize,
    pub vacuum_reports: Vec<SessionVacuumReport>,
    pub total_bytes_saved: i64,
    pub ok: bool,
}

pub struct SessionCompactor;

impl SessionCompactor {
    /// Executa VACUUM completo e seguro em um banco SQLite
    pub fn vacuum_database(db_path: &Path) -> Result<SessionVacuumReport, String> {
        if !db_path.exists() {
            return Err(format!(
                "Database file {} does not exist",
                db_path.display()
            ));
        }

        let start = std::time::Instant::now();
        let bytes_before = fs::metadata(db_path).map(|m| m.len()).unwrap_or(0);

        let conn = Connection::open(db_path)
            .map_err(|e| format!("Failed to open {}: {e}", db_path.display()))?;

        // Métricas antes
        let (page_count_before, freelist_before): (i64, i64) = conn
            .query_row(
                "SELECT (SELECT page_count FROM pragma_page_count()), (SELECT freelist_count FROM pragma_freelist_count());",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap_or((0, 0));

        // 1. PASSIVE checkpoint para garantir flush seguro antes do VACUUM
        let _ = conn.execute_batch("PRAGMA wal_checkpoint(PASSIVE);");

        // 2. VACUUM
        conn.execute_batch("VACUUM;")
            .map_err(|e| format!("VACUUM failed on {}: {e}", db_path.display()))?;

        // 3. TRUNCATE checkpoint para recuperar arquivo -wal após o VACUUM
        let _ = conn.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);");

        let (page_count_after, freelist_after): (i64, i64) = conn
            .query_row(
                "SELECT (SELECT page_count FROM pragma_page_count()), (SELECT freelist_count FROM pragma_freelist_count());",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap_or((0, 0));

        drop(conn);

        let bytes_after = fs::metadata(db_path).map(|m| m.len()).unwrap_or(0);

        let bytes_saved = (bytes_before as i64) - (bytes_after as i64);

        Ok(SessionVacuumReport {
            db_path: db_path.to_string_lossy().to_string(),
            bytes_before,
            bytes_after,
            bytes_saved,
            page_count_before,
            page_count_after,
            freelist_before,
            freelist_after,
            duration_ms: start.elapsed().as_millis(),
            success: true,
        })
    }

    /// Executa VACUUM em todos os bancos de dados conhecidos do HAOS
    pub fn vacuum_all_known_dbs(data_dir: &Path) -> Vec<SessionVacuumReport> {
        let candidate_dbs = [
            data_dir.join("state.db"),
            data_dir.join("kanban.db"),
            data_dir.join("memory").join("ragflow.db"),
            data_dir.join("memory").join("graphrag.db"),
            data_dir.join("memory").join("reconciled_memories.db"),
        ];

        let mut reports = Vec::new();
        for db in &candidate_dbs {
            if db.exists() {
                if let Ok(report) = Self::vacuum_database(db) {
                    reports.push(report);
                }
            }
        }
        reports
    }

    /// Verifica a origem (source) da sessão no state.db
    pub fn get_session_source(state_db: &Path, session_id: &str) -> Option<String> {
        if !state_db.exists() {
            return None;
        }
        let conn = Connection::open_with_flags(
            state_db,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .ok()?;
        conn.query_row(
            "SELECT source FROM sessions WHERE id = ?1;",
            [session_id],
            |r| r.get(0),
        )
        .ok()
    }

    /// Executa auto-shrink de um único arquivo de sessão WebUI (.json) de forma coordenada
    pub fn shrink_webui_session(
        session_file: &Path,
        state_db: &Path,
        pct: f64,
        min_keep: usize,
        dry_run: bool,
    ) -> Result<Option<WebUiSessionShrinkResult>, String> {
        let session_id = session_file
            .file_stem()
            .and_then(|s| s.to_str())
            .ok_or_else(|| "Invalid filename stem".to_string())?;

        let size_before = fs::metadata(session_file).map(|m| m.len()).unwrap_or(0);

        let content = fs::read_to_string(session_file)
            .map_err(|e| format!("Failed to read {}: {e}", session_file.display()))?;

        let mut data: serde_json::Value = serde_json::from_str(&content)
            .map_err(|e| format!("Invalid JSON {}: {e}", session_file.display()))?;

        // Guardas de segurança
        if data
            .get("active_stream_id")
            .and_then(|v| v.as_str())
            .is_some()
            || data
                .get("pending_user_message")
                .and_then(|v| v.as_str())
                .is_some()
        {
            return Ok(None); // Conversa ativa em curso
        }

        // Guarda: apenas sessões 'webui'
        if let Some(src) = Self::get_session_source(state_db, session_id) {
            if src != "webui" {
                return Ok(None);
            }
        }

        let title = data
            .get("title")
            .and_then(|t| t.as_str())
            .map(|s| s.to_string());

        let msgs = match data.get_mut("messages").and_then(|v| v.as_array_mut()) {
            Some(m) => m,
            None => return Ok(None),
        };

        let total_msgs = msgs.len();
        if total_msgs < 8 {
            return Ok(None);
        }

        let mut user_indices = Vec::new();
        for (i, m) in msgs.iter().enumerate() {
            if m.get("role").and_then(|r| r.as_str()) == Some("user") {
                user_indices.push(i);
            }
        }

        if user_indices.is_empty() {
            return Ok(None);
        }

        let target_idx = ((total_msgs as f64) * pct) as usize;
        let cutoff = user_indices.iter().copied().find(|&i| i >= target_idx);
        let cutoff = match cutoff {
            Some(c) if c > 0 => c,
            _ => return Ok(None),
        };

        let kept_slice = &msgs[cutoff..];
        if kept_slice.len() < min_keep {
            return Ok(None);
        }

        let cutoff_ts = kept_slice[0]
            .get("timestamp")
            .and_then(|t| t.as_f64())
            .unwrap_or(0.0);

        if cutoff_ts <= 0.0 {
            return Ok(None);
        }

        // Monotonicidade: nenhuma mantida com ts anterior a cutoff_ts
        let non_monotonic = kept_slice.iter().any(|m| {
            m.get("timestamp")
                .and_then(|t| t.as_f64())
                .map(|ts| ts < cutoff_ts)
                .unwrap_or(false)
        });
        if non_monotonic {
            return Ok(None);
        }

        let messages_before = total_msgs;
        let messages_after = total_msgs - cutoff;
        let messages_removed = cutoff;

        if dry_run {
            return Ok(Some(WebUiSessionShrinkResult {
                session_id: session_id.to_string(),
                title,
                size_bytes_before: size_before,
                size_bytes_after: size_before,
                messages_before,
                messages_after,
                messages_removed,
                db_rows_deleted: 0,
                backup_path: None,
            }));
        }

        // ── 1. Backup rotativo ──
        let sessions_dir = session_file.parent().unwrap_or_else(|| Path::new("."));
        let bak_dir = sessions_dir.join("_auto_bak");
        let _ = fs::create_dir_all(&bak_dir);

        let now_epoch = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let bak_file = bak_dir.join(format!("{session_id}.{now_epoch}.json"));
        let _ = fs::copy(session_file, &bak_file);

        // ── 2. Expurgo e contagem transacional no state.db ──
        let mut db_rows_deleted = 0;
        if state_db.exists() {
            if let Ok(mut conn) = Connection::open(state_db) {
                // Coleta IDs que serão mantidos
                let mut keep_row_ids: Vec<i64> = Vec::new();
                for m in kept_slice {
                    if let Some(row_id) = m.get("_row_id").and_then(|v| v.as_i64()) {
                        keep_row_ids.push(row_id);
                    }
                }

                if let Ok(tx) = conn.transaction() {
                    let d1 = tx
                        .execute(
                            "DELETE FROM messages WHERE session_id = ?1 AND timestamp < ?2;",
                            rusqlite::params![session_id, cutoff_ts],
                        )
                        .unwrap_or(0);
                    db_rows_deleted += d1;

                    if !keep_row_ids.is_empty() {
                        let _ = tx.execute_batch(
                            "CREATE TEMP TABLE IF NOT EXISTS _shrink_keep(id INTEGER PRIMARY KEY);",
                        );
                        let _ = tx.execute_batch("DELETE FROM _shrink_keep;");
                        {
                            if let Ok(mut stmt) =
                                tx.prepare("INSERT OR IGNORE INTO _shrink_keep(id) VALUES (?1);")
                            {
                                for id in &keep_row_ids {
                                    let _ = stmt.execute([id]);
                                }
                            }
                        }
                        let d2 = tx.execute(
                            "DELETE FROM messages WHERE session_id = ?1 AND id NOT IN (SELECT id FROM _shrink_keep);",
                            rusqlite::params![session_id],
                        ).unwrap_or(0);
                        db_rows_deleted += d2;
                        let _ = tx.execute_batch("DROP TABLE IF EXISTS _shrink_keep;");
                    }

                    let _ = tx.execute(
                        "UPDATE sessions SET message_count = ?1 WHERE id = ?2;",
                        rusqlite::params![messages_after as i64, session_id],
                    );

                    let _ = tx.execute(
                        "DELETE FROM session_model_usage WHERE session_id = ?1 AND last_seen < ?2;",
                        rusqlite::params![session_id, cutoff_ts],
                    );

                    let _ = tx.commit();
                }
            }
        }

        // ── 3. Atualização do Sidecar JSON ──
        let kept_vec: Vec<serde_json::Value> = kept_slice.to_vec();
        data["messages"] = serde_json::Value::Array(kept_vec);
        data["message_count"] = serde_json::json!(messages_after);

        // Ajusta tool_calls offset
        if let Some(tcs) = data.get("tool_calls").and_then(|t| t.as_array()) {
            let mut kept_tc = Vec::new();
            for tc in tcs {
                if let Some(idx) = tc.get("assistant_msg_idx").and_then(|i| i.as_i64()) {
                    if (idx as usize) >= cutoff {
                        let mut tc_cloned = tc.clone();
                        tc_cloned["assistant_msg_idx"] = serde_json::json!(idx - (cutoff as i64));
                        kept_tc.push(tc_cloned);
                    }
                }
            }
            data["tool_calls"] = serde_json::Value::Array(kept_tc);
        }

        // Ajusta anchor_activity_scenes
        if let Some(aas) = data
            .get("anchor_activity_scenes")
            .and_then(|a| a.as_object())
        {
            let mut kept_aas = serde_json::Map::new();
            for (k, v) in aas {
                if let Some(idx) = v.get("message_index").and_then(|i| i.as_i64()) {
                    if (idx as usize) >= cutoff {
                        let mut v_cloned = v.clone();
                        v_cloned["message_index"] = serde_json::json!(idx - (cutoff as i64));
                        kept_aas.insert(k.clone(), v_cloned);
                    }
                }
            }
            data["anchor_activity_scenes"] = serde_json::Value::Object(kept_aas);
        }

        // Atualiza marcas de corte
        data["truncation_boundary"] = serde_json::json!(cutoff_ts);
        data["intentional_shrink_generation"] =
            serde_json::json!(format!("rust-shrink-{now_epoch}"));
        data["active_stream_id"] = serde_json::Value::Null;
        data["pending_user_message"] = serde_json::Value::Null;

        // Escrita atômica via arquivo temporário
        let tmp_path = session_file.with_extension(format!("tmp.shrink.{}", std::process::id()));
        let serialized = serde_json::to_string_pretty(&data)
            .map_err(|e| format!("Failed to serialize {}: {e}", session_file.display()))?;

        {
            let mut f = File::create(&tmp_path)
                .map_err(|e| format!("Failed to create tmp file {}: {e}", tmp_path.display()))?;
            f.write_all(serialized.as_bytes())
                .map_err(|e| format!("Failed to write tmp file: {e}"))?;
            f.flush().map_err(|e| format!("Flush failed: {e}"))?;
        }
        fs::rename(&tmp_path, session_file).map_err(|e| {
            format!(
                "Failed to atomically replace {}: {e}",
                session_file.display()
            )
        })?;

        // ── 4. Atualiza _index.json da sidebar do WebUI ──
        let index_path = sessions_dir.join("_index.json");
        if index_path.exists() {
            if let Ok(idx_str) = fs::read_to_string(&index_path) {
                if let Ok(mut idx_val) = serde_json::from_str::<serde_json::Value>(&idx_str) {
                    if let Some(arr) = idx_val.as_array_mut() {
                        for item in arr.iter_mut() {
                            if item.get("session_id").and_then(|s| s.as_str()) == Some(session_id) {
                                item["message_count"] = serde_json::json!(messages_after);
                                break;
                            }
                        }
                        if let Ok(new_idx_str) = serde_json::to_string_pretty(&idx_val) {
                            let _ = fs::write(&index_path, new_idx_str);
                        }
                    }
                }
            }
        }

        let size_after = fs::metadata(session_file).map(|m| m.len()).unwrap_or(0);

        Ok(Some(WebUiSessionShrinkResult {
            session_id: session_id.to_string(),
            title,
            size_bytes_before: size_before,
            size_bytes_after: size_after,
            messages_before,
            messages_after,
            messages_removed,
            db_rows_deleted,
            backup_path: Some(bak_file.to_string_lossy().to_string()),
        }))
    }

    /// Varre diretório de sessões do WebUI e executa auto-compactação nas que excedem limit_mb
    pub fn scan_and_compact_webui_sessions(
        sessions_dir: &Path,
        state_db: &Path,
        limit_mb: f64,
        pct: f64,
        min_keep: usize,
        recent_minutes: f64,
        dry_run: bool,
    ) -> (Vec<WebUiSessionShrinkResult>, usize) {
        if !sessions_dir.exists() {
            return (Vec::new(), 0);
        }

        let mut results = Vec::new();
        let mut candidates_count = 0;
        let limit_bytes = (limit_mb * 1024.0 * 1024.0) as u64;
        let now_epoch = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();

        let entries = match fs::read_dir(sessions_dir) {
            Ok(e) => e,
            Err(_) => return (Vec::new(), 0),
        };

        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }
            if path.extension().and_then(|s| s.to_str()) != Some("json") {
                continue;
            }
            let file_name = match path.file_name().and_then(|s| s.to_str()) {
                Some(n) => n,
                None => continue,
            };
            if file_name.starts_with('_')
                || file_name.contains(".bak")
                || file_name.contains(".tmp")
            {
                continue;
            }

            let meta = match fs::metadata(&path) {
                Ok(m) => m,
                Err(_) => continue,
            };

            if meta.len() < limit_bytes {
                continue;
            }

            candidates_count += 1;

            // Verifica updated_at para pular sessões ativas nos últimos `recent_minutes`
            if let Ok(head_str) = fs::read_to_string(&path) {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&head_str) {
                    if let Some(updated_at) = v.get("updated_at").and_then(|u| u.as_f64()) {
                        if (now_epoch - updated_at) < (recent_minutes * 60.0) {
                            continue;
                        }
                    }
                }
            }

            if let Ok(Some(shrink_res)) =
                Self::shrink_webui_session(&path, state_db, pct, min_keep, dry_run)
            {
                results.push(shrink_res);
            }
        }

        (results, candidates_count)
    }

    /// Executa ciclo completo de auto-manutenção: compactação de sessões WebUI + VACUUM dos SQLite DBs
    pub fn run_auto_maintenance(
        data_dir: &Path,
        limit_mb: f64,
        pct: f64,
        min_keep: usize,
        recent_minutes: f64,
        dry_run: bool,
    ) -> AutoMaintenanceReport {
        let state_db = data_dir.join("state.db");
        let webui_sessions_dir = data_dir.join("webui").join("sessions");

        let (shrunk_sessions, candidate_sessions_count) = Self::scan_and_compact_webui_sessions(
            &webui_sessions_dir,
            &state_db,
            limit_mb,
            pct,
            min_keep,
            recent_minutes,
            dry_run,
        );

        let mut vacuum_reports = Vec::new();
        let mut total_bytes_saved: i64 = 0;

        for s in &shrunk_sessions {
            total_bytes_saved += (s.size_bytes_before as i64) - (s.size_bytes_after as i64);
        }

        if !dry_run {
            let db_reports = Self::vacuum_all_known_dbs(data_dir);
            for rep in db_reports {
                total_bytes_saved += rep.bytes_saved;
                vacuum_reports.push(rep);
            }
        }

        AutoMaintenanceReport {
            shrunk_sessions,
            candidate_sessions_count,
            vacuum_reports,
            total_bytes_saved,
            ok: true,
        }
    }
}
