use rayon::prelude::*;
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::fs::File;
use std::io::BufReader;
use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};
use std::time::{Duration, Instant};

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct WebUISessionItem {
    pub session_id: String,
    pub title: String,
    pub workspace: Option<String>,
    pub model: Option<String>,
    pub model_provider: Option<String>,
    pub message_count: usize,
    pub created_at: Option<f64>,
    pub updated_at: Option<f64>,
    pub last_message_at: Option<f64>,
    pub pinned: bool,
    pub archived: bool,
    pub project_id: Option<Value>,
    pub profile: Option<String>,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub estimated_cost: f64,
    pub cache_read_tokens: u64,
    pub cache_write_tokens: u64,
    pub cache_hit_percent: u32,
    pub personality: Option<Value>,
    pub pre_compression_snapshot: bool,
    pub context_length: u64,
    pub gateway_routing: Option<Value>,
    pub user_message_count: usize,
    pub active_stream_id: Option<Value>,
    pub has_pending_user_message: bool,
    pub is_cli_session: bool,
    pub source_tag: Option<String>,
    pub raw_source: Option<String>,
    pub session_source: Option<String>,
    pub source_label: Option<String>,
    pub read_only: bool,
    pub worktree_branch: Option<String>,
    pub worktree_path: Option<String>,
    pub worktree_created_at: Option<f64>,
    pub worktree_repo_root: Option<String>,
}

#[derive(Clone)]
pub struct SessionCache {
    inner: Arc<RwLock<Option<(Instant, Vec<WebUISessionItem>)>>>,
}

impl SessionCache {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(RwLock::new(None)),
        }
    }

    pub fn get_or_refresh(&self, haos_home: &Path) -> Vec<WebUISessionItem> {
        let ttl = Duration::from_secs(2);
        {
            if let Ok(guard) = self.inner.read() {
                if let Some((instant, ref items)) = *guard {
                    if instant.elapsed() < ttl {
                        return items.clone();
                    }
                }
            }
        }

        let fresh = SessionManager::list_all_sessions(haos_home);
        if let Ok(mut guard) = self.inner.write() {
            *guard = Some((Instant::now(), fresh.clone()));
        }
        fresh
    }
}

pub struct SessionManager;

impl SessionManager {
    pub fn resolve_haos_home() -> PathBuf {
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
        PathBuf::from("/root/.haos")
    }

    pub fn list_all_sessions(haos_home: &Path) -> Vec<WebUISessionItem> {
        let webui_sessions_dir = haos_home.join("webui").join("sessions");
        let state_db_path = haos_home.join("state.db");

        // 1. Ler todos os sidecars JSON do WebUI de forma paralela via Rayon
        let mut webui_sessions: HashMap<String, WebUISessionItem> = HashMap::new();
        if webui_sessions_dir.exists() {
            if let Ok(entries) = std::fs::read_dir(&webui_sessions_dir) {
                let paths: Vec<PathBuf> = entries
                    .flatten()
                    .map(|e| e.path())
                    .filter(|p| {
                        if let Some(name) = p.file_name().and_then(|n| n.to_str()) {
                            name.ends_with(".json") && !name.starts_with('_')
                        } else {
                            false
                        }
                    })
                    .collect();

                let items: Vec<WebUISessionItem> = paths
                    .par_iter()
                    .filter_map(|p| Self::parse_webui_json(p))
                    .collect();

                for item in items {
                    webui_sessions.insert(item.session_id.clone(), item);
                }
            }
        }

        // 2. Consultar sessões do SQLite state.db
        if state_db_path.exists() {
            if let Ok(conn) = Connection::open_with_flags(
                &state_db_path,
                OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
            ) {
                let query = "SELECT id, source, title, model, started_at, last_activity_at,
                                    message_count, input_tokens, output_tokens,
                                    cache_read_tokens, cache_write_tokens,
                                    estimated_cost_usd, pinned, archived, cwd
                             FROM sessions
                             WHERE hidden = 0
                             ORDER BY pinned DESC, COALESCE(last_activity_at, started_at) DESC;";

                if let Ok(mut stmt) = conn.prepare(query) {
                    let rows = stmt.query_map([], |row| {
                        let id: String = row.get(0)?;
                        let source: String = row.get(1)?;
                        let title: Option<String> = row.get(2)?;
                        let model: Option<String> = row.get(3)?;
                        let started_at: f64 = row.get(4)?;
                        let last_activity_at: Option<f64> = row.get(5)?;
                        let message_count: i64 = row.get(6).unwrap_or(0);
                        let input_tokens: i64 = row.get(7).unwrap_or(0);
                        let output_tokens: i64 = row.get(8).unwrap_or(0);
                        let cache_read: i64 = row.get(9).unwrap_or(0);
                        let cache_write: i64 = row.get(10).unwrap_or(0);
                        let cost: Option<f64> = row.get(11)?;
                        let pinned: i64 = row.get(12).unwrap_or(0);
                        let archived: i64 = row.get(13).unwrap_or(0);
                        let cwd: Option<String> = row.get(14)?;

                        Ok((
                            id,
                            source,
                            title,
                            model,
                            started_at,
                            last_activity_at,
                            message_count,
                            input_tokens,
                            output_tokens,
                            cache_read,
                            cache_write,
                            cost,
                            pinned != 0,
                            archived != 0,
                            cwd,
                        ))
                    });

                    if let Ok(mapped) = rows {
                        for r in mapped.flatten() {
                            let (
                                id,
                                source,
                                title,
                                model,
                                started_at,
                                last_activity_at,
                                msg_count,
                                in_tok,
                                out_tok,
                                c_read,
                                c_write,
                                cost,
                                is_pinned,
                                is_archived,
                                cwd,
                            ) = r;

                            if let Some(existing) = webui_sessions.get_mut(&id) {
                                if existing.title.is_empty() || existing.title == "Nova Sessão" {
                                    if let Some(t) = title {
                                        existing.title = t;
                                    }
                                }
                                if is_pinned {
                                    existing.pinned = true;
                                }
                            } else {
                                let display_title = title.unwrap_or_else(|| "Sessão Agêntica".to_string());
                                let is_cli = source != "webui";
                                let source_label = match source.as_str() {
                                    "haos" => "HAOS",
                                    "subagent" => "Subagent",
                                    "cron" => "Cron",
                                    "webui" => "WebUI",
                                    other => other,
                                };

                                webui_sessions.insert(
                                    id.clone(),
                                    WebUISessionItem {
                                        session_id: id,
                                        title: display_title,
                                        workspace: cwd,
                                        model,
                                        model_provider: Some("custom:wrapper".to_string()),
                                        message_count: msg_count as usize,
                                        created_at: Some(started_at),
                                        updated_at: last_activity_at.or(Some(started_at)),
                                        last_message_at: last_activity_at.or(Some(started_at)),
                                        pinned: is_pinned,
                                        archived: is_archived,
                                        project_id: None,
                                        profile: Some("default".to_string()),
                                        input_tokens: in_tok as u64,
                                        output_tokens: out_tok as u64,
                                        estimated_cost: cost.unwrap_or(0.0),
                                        cache_read_tokens: c_read as u64,
                                        cache_write_tokens: c_write as u64,
                                        cache_hit_percent: 0,
                                        personality: None,
                                        pre_compression_snapshot: false,
                                        context_length: 1_000_000,
                                        gateway_routing: None,
                                        user_message_count: 1,
                                        active_stream_id: None,
                                        has_pending_user_message: false,
                                        is_cli_session: is_cli,
                                        source_tag: Some(source.clone()),
                                        raw_source: Some(source.clone()),
                                        session_source: Some(source.clone()),
                                        source_label: Some(source_label.to_string()),
                                        read_only: is_cli,
                                        worktree_branch: None,
                                        worktree_path: None,
                                        worktree_created_at: None,
                                        worktree_repo_root: None,
                                    },
                                );
                            }
                        }
                    }
                }
            }
        }

        // 3. Converter para lista ordenada: PINNED primeiro, depois UPDATED_AT decrescente
        let mut list: Vec<WebUISessionItem> = webui_sessions.into_values().collect();
        list.sort_by(|a, b| {
            b.pinned
                .cmp(&a.pinned)
                .then_with(|| {
                    let ts_a = a.updated_at.or(a.created_at).unwrap_or(0.0);
                    let ts_b = b.updated_at.or(b.created_at).unwrap_or(0.0);
                    ts_b.partial_cmp(&ts_a).unwrap_or(std::cmp::Ordering::Equal)
                })
        });

        list
    }

    fn parse_webui_json(path: &Path) -> Option<WebUISessionItem> {
        let file = File::open(path).ok()?;
        let reader = BufReader::new(file);
        let val: Value = serde_json::from_reader(reader).ok()?;

        if !val.is_object() {
            return None;
        }

        let sid = val
            .get("session_id")
            .and_then(|v| v.as_str())
            .map(String::from)
            .or_else(|| {
                path.file_stem()
                    .and_then(|s| s.to_str())
                    .map(String::from)
            })?;

        let title = val
            .get("title")
            .and_then(|v| v.as_str())
            .unwrap_or("Nova Sessão")
            .to_string();

        let workspace = val.get("workspace").and_then(|v| v.as_str()).map(String::from);
        let model = val.get("model").and_then(|v| v.as_str()).map(String::from);
        let model_provider = val.get("model_provider").and_then(|v| v.as_str()).map(String::from);

        let message_count = val
            .get("messages")
            .and_then(|v| v.as_array())
            .map(|a| a.len())
            .unwrap_or(0);

        let created_at = val.get("created_at").and_then(|v| v.as_f64());
        let updated_at = val.get("updated_at").and_then(|v| v.as_f64());
        let last_message_at = val.get("last_message_at").and_then(|v| v.as_f64()).or(updated_at);
        let pinned = val.get("pinned").and_then(|v| v.as_bool()).unwrap_or(false);
        let archived = val.get("archived").and_then(|v| v.as_bool()).unwrap_or(false);
        let project_id = val.get("project_id").cloned();
        let profile = val.get("profile").and_then(|v| v.as_str()).map(String::from).or(Some("default".to_string()));

        let input_tokens = val.get("input_tokens").and_then(|v| v.as_u64()).unwrap_or(0);
        let output_tokens = val.get("output_tokens").and_then(|v| v.as_u64()).unwrap_or(0);
        let estimated_cost = val.get("estimated_cost").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let cache_read_tokens = val.get("cache_read_tokens").and_then(|v| v.as_u64()).unwrap_or(0);
        let cache_write_tokens = val.get("cache_write_tokens").and_then(|v| v.as_u64()).unwrap_or(0);

        let cache_hit_percent = val.get("cache_hit_percent").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
        let personality = val.get("personality").cloned();
        let pre_compression_snapshot = val.get("pre_compression_snapshot").and_then(|v| v.as_bool()).unwrap_or(false);
        let context_length = val.get("context_length").and_then(|v| v.as_u64()).unwrap_or(1_000_000);
        let gateway_routing = val.get("gateway_routing").cloned();
        let user_message_count = val.get("user_message_count").and_then(|v| v.as_u64()).unwrap_or(1) as usize;
        let active_stream_id = val.get("active_stream_id").cloned();
        let has_pending_user_message = val.get("has_pending_user_message").and_then(|v| v.as_bool()).unwrap_or(false);
        let is_cli_session = val.get("is_cli_session").and_then(|v| v.as_bool()).unwrap_or(false);
        let source_tag = val.get("source_tag").and_then(|v| v.as_str()).map(String::from).or(Some("webui".to_string()));
        let raw_source = val.get("raw_source").and_then(|v| v.as_str()).map(String::from).or(Some("webui".to_string()));
        let session_source = val.get("session_source").and_then(|v| v.as_str()).map(String::from).or(Some("webui".to_string()));
        let source_label = val.get("source_label").and_then(|v| v.as_str()).map(String::from).or(Some("WebUI".to_string()));
        let read_only = val.get("read_only").and_then(|v| v.as_bool()).unwrap_or(false);

        let worktree_branch = val.get("worktree_branch").and_then(|v| v.as_str()).map(String::from);
        let worktree_path = val.get("worktree_path").and_then(|v| v.as_str()).map(String::from);
        let worktree_created_at = val.get("worktree_created_at").and_then(|v| v.as_f64());
        let worktree_repo_root = val.get("worktree_repo_root").and_then(|v| v.as_str()).map(String::from);

        Some(WebUISessionItem {
            session_id: sid,
            title,
            workspace,
            model,
            model_provider,
            message_count,
            created_at,
            updated_at,
            last_message_at,
            pinned,
            archived,
            project_id,
            profile,
            input_tokens,
            output_tokens,
            estimated_cost,
            cache_read_tokens,
            cache_write_tokens,
            cache_hit_percent,
            personality,
            pre_compression_snapshot,
            context_length,
            gateway_routing,
            user_message_count,
            active_stream_id,
            has_pending_user_message,
            is_cli_session,
            source_tag,
            raw_source,
            session_source,
            source_label,
            read_only,
            worktree_branch,
            worktree_path,
            worktree_created_at,
            worktree_repo_root,
        })
    }
}
