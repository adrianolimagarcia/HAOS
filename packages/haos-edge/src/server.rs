use crate::auth;
use crate::blast_analyzer::FastAstAnalyzer;
use crate::cancel_registry::CancelRegistry;
use crate::compactor::{CompactPayload, ContextCompactor};
use crate::context_hasher::ContextHasher;
use crate::cron_ledger::CronLedgerEngine;
use crate::db::DbHelper;
use crate::event_hub::{EventHub, PlatformEvent};
use crate::idempotency::IdempotencyEngine;
use crate::loop_detector::LoopDetector;
use crate::pty::PtyManager;
use crate::system_one::{DecisionRecord, SystemOneEngine};
use crate::worker_snapshot;
use crate::worktree_engine::NativeWorktreeEngine;
use crate::writer_envelope::WriterBinding;
use crate::writer_executor::{WriterError, WriterExecutor};
use axum::extract::{Path as AxPath, Query, State};
use axum::http::Request;
use axum::http::{header, HeaderMap, StatusCode};
use axum::middleware::{self, Next};
use axum::response::{sse::Event, Html, IntoResponse, Json, Response, Sse};
use axum::routing::{get, post};
use axum::Router;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::File;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::Mutex as TokioMutex;
use tower_http::cors::CorsLayer;
use tower_http::services::ServeDir;

#[derive(Clone)]
pub struct AppState {
    pub pty_manager: Arc<PtyManager>,
    pub event_hub: Arc<EventHub>,
    pub cancel_registry: Arc<CancelRegistry>,
    pub loop_detectors: Arc<TokioMutex<HashMap<String, LoopDetector>>>,
    pub system_one: Arc<SystemOneEngine>,
    pub idempotency: Arc<IdempotencyEngine>,
    pub cron_ledger: Arc<CronLedgerEngine>,
    pub transport_ingress: Arc<crate::transport_ingress::TransportIngress>,
    pub static_dir: PathBuf,
    pub data_dir: PathBuf,
    pub sessions_dbs: Vec<PathBuf>,
    pub profile: String,
    pub observer_only: bool,
    pub upstream_url: Option<String>,
    pub gateway_upstream_url: Option<String>,
    pub http_client: reqwest::Client,
    /// Present only when the explicit rust writer mode is selected. Python remains the default.
    pub writer_executor: Option<Arc<WriterExecutor>>,
    pub writer_token: Option<String>,
}

#[derive(Deserialize)]
pub struct StartRequest {
    pub cwd: Option<String>,
    pub env: Option<HashMap<String, String>>,
}

#[derive(Deserialize)]
pub struct InputRequest {
    pub data: String,
}

#[derive(Deserialize)]
pub struct ResizeRequest {
    pub rows: u16,
    pub cols: u16,
}

#[derive(Serialize)]
pub struct DrainResponse {
    pub data: String,
    pub running: bool,
    pub exit_code: i32,
}

/// Auto-descoberta universal de host em cascata:
/// 1. Se especificado IP explícito (ex: 0.0.0.0, 192.168.1.10, 127.0.0.1), usa direto.
/// 2. Se "auto" / "tailnet" / "tailscale":
///    - Procura interface e IP ativo do Tailscale (100.x.y.z)
///    - Se não houver Tailscale, descobre o IP da LAN privada ativa (192.168.x.x, 10.x.x.x, 172.16-31.x.x)
///    - Se estiver offline/isolado, faz fallback seguro para 127.0.0.1
pub fn resolve_bind_host(host_input: &str) -> String {
    let trimmed = host_input.trim();
    if trimmed.eq_ignore_ascii_case("auto")
        || trimmed.eq_ignore_ascii_case("tailnet")
        || trimmed.eq_ignore_ascii_case("tailscale")
    {
        // Nível 1: Tenta obter IP do Tailscale
        if let Ok(output) = std::process::Command::new("tailscale")
            .args(["ip", "-4"])
            .output()
        {
            if output.status.success() {
                let ip_str = String::from_utf8_lossy(&output.stdout).trim().to_string();
                if !ip_str.is_empty() && ip_str.parse::<std::net::IpAddr>().is_ok() {
                    return ip_str;
                }
            }
        }
        if let Ok(output) = std::process::Command::new("ip")
            .args(["-4", "addr", "show", "tailscale0"])
            .output()
        {
            if output.status.success() {
                let text = String::from_utf8_lossy(&output.stdout);
                for line in text.lines() {
                    let trimmed_line = line.trim();
                    if trimmed_line.starts_with("inet ") {
                        let parts: Vec<&str> = trimmed_line.split_whitespace().collect();
                        if parts.len() >= 2 {
                            if let Some(ip_part) = parts[1].split('/').next() {
                                if ip_part.parse::<std::net::IpAddr>().is_ok() {
                                    return ip_part.to_string();
                                }
                            }
                        }
                    }
                }
            }
        }

        // Nível 2 (sem Tailscale): Procura IP de LAN privada via probes UDP (RFC 1918)
        let probes = [
            "192.168.255.255:80",
            "10.255.255.255:80",
            "172.31.255.255:80",
            "1.1.1.1:80",
        ];
        for probe in probes {
            if let Ok(socket) = std::net::UdpSocket::bind("0.0.0.0:0") {
                if socket.connect(probe).is_ok() {
                    if let Ok(local_addr) = socket.local_addr() {
                        let ip = local_addr.ip();
                        if let std::net::IpAddr::V4(ipv4) = ip {
                            if !ipv4.is_loopback() && (ipv4.is_private() || ipv4.octets()[0] == 100)
                            {
                                return ipv4.to_string();
                            }
                        }
                    }
                }
            }
        }

        // Nível 3: Fallback final seguro para localhost
        return "127.0.0.1".to_string();
    }
    trimmed.to_string()
}

pub async fn run_server(
    port: u16,
    host: &str,
    static_path: Option<PathBuf>,
    upstream: Option<String>,
    gateway_upstream: Option<String>,
    data_dir: PathBuf,
    profile: String,
    observer_only: bool,
    writer_mode: String,
    sessions_dbs: Vec<PathBuf>,
) -> Result<(), String> {
    let primary_db = data_dir.join("state.db");
    let mut seen_session_dbs = std::collections::HashSet::new();
    seen_session_dbs.insert(primary_db.canonicalize().unwrap_or(primary_db.clone()));
    let sessions_dbs = sessions_dbs
        .into_iter()
        .filter_map(|path| path.canonicalize().ok())
        .filter(|path| path.is_file() && seen_session_dbs.insert(path.clone()))
        .collect::<Vec<_>>();
    if !observer_only {
        let _ = std::fs::create_dir_all(&data_dir);

        let lock_path = data_dir.join(format!("controlplane_{port}.lock"));
        let lock_file =
            File::create(&lock_path).map_err(|e| format!("Failed to create lock file: {e}"))?;
        unsafe {
            let fd = std::os::fd::AsRawFd::as_raw_fd(&lock_file);
            if libc::flock(fd, libc::LOCK_EX | libc::LOCK_NB) != 0 {
                return Err(format!(
                    "Control plane is already running on port {port} (locked by another process)"
                ));
            }
        }
        auth::ensure_sessions_dir(&data_dir)
            .map_err(|e| format!("Failed to init sessions dir: {e}"))?;
    }

    // Resolve static files directory
    let static_dir = if let Some(p) = static_path {
        p
    } else {
        let candidates = [
            PathBuf::from("/opt/haos/hermes/platform/webui/static"),
            PathBuf::from("hermes/platform/webui/static"),
            PathBuf::from("/usr/local/lib/haos-agent/hermes/platform/webui/static"),
        ];
        candidates
            .into_iter()
            .find(|p| p.exists())
            .unwrap_or_else(|| PathBuf::from("static"))
    };

    let upstream_url = upstream
        .or_else(|| std::env::var("HAOS_UPSTREAM_URL").ok())
        .or_else(|| std::env::var("HAOS_UPSTREAM_WEBUI").ok());

    let gateway_upstream_url =
        gateway_upstream.or_else(|| std::env::var("HAOS_UPSTREAM_GATEWAY").ok());

    let http_client = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(5))
        .build()
        .map_err(|e| format!("Failed to create reqwest client: {e}"))?;

    let pty_manager = Arc::new(PtyManager::new());
    let rust_writer = match writer_mode.as_str() {
        "python" | "shadow" => false,
        "rust" => true,
        _ => return Err("writer mode must be python, shadow, or rust".to_string()),
    };
    let effective_observer_only = observer_only;
    let writer_token = if rust_writer {
        Some(std::env::var("HAOS_RUST_WRITER_TOKEN").map_err(|_| {
            "rust writer mode requires HAOS_RUST_WRITER_TOKEN".to_string()
        })?)
    } else {
        None
    };
    let writer_executor = if rust_writer {
        Some(Arc::new(
            WriterExecutor::open(&data_dir, &profile)
                .map_err(|e| format!("failed to acquire rust writer: {e}"))?,
        ))
    } else {
        None
    };
    let event_hub = if effective_observer_only || rust_writer {
        Arc::new(EventHub::new_observer(data_dir.join("events.db")))
    } else {
        Arc::new(EventHub::new(data_dir.join("events.db")))
    };
    let cancel_registry = Arc::new(CancelRegistry::new());
    let loop_detectors = Arc::new(TokioMutex::new(HashMap::new()));
    let system_one = Arc::new(if effective_observer_only {
        SystemOneEngine::new_uninitialized(&data_dir)
    } else {
        SystemOneEngine::new(&data_dir)
    });
    let idempotency = Arc::new(if effective_observer_only {
        IdempotencyEngine::new_uninitialized(&data_dir)
    } else {
        IdempotencyEngine::new(&data_dir)
    });
    let cron_ledger = Arc::new(CronLedgerEngine::new(&data_dir));
    let transport_ingress = Arc::new(crate::transport_ingress::TransportIngress::new(
        (*event_hub).clone(),
    ));
    let state = AppState {
        pty_manager,
        event_hub,
        cancel_registry,
        loop_detectors,
        system_one,
        idempotency,
        cron_ledger,
        transport_ingress,
        static_dir: static_dir.clone(),
        data_dir: data_dir.clone(),
        sessions_dbs: sessions_dbs.clone(),
        profile,
        observer_only: effective_observer_only,
        upstream_url: upstream_url.clone(),
        gateway_upstream_url: gateway_upstream_url.clone(),
        http_client,
        writer_executor,
        writer_token,
    };

    // The observer owns no writer/checkpointer. Python remains the sole SQLite writer.

    let app = if effective_observer_only {
        let mut observer = Router::new()
            .route("/health", get(health_handler))
            .route("/login", get(login_page_handler))
            .route("/api/login", post(login_handler))
            .route("/api/logout", post(logout_handler))
            .route("/api/sessions/fast", get(sessions_fast_handler))
            .route("/api/events/stream", get(event_stream_handler))
            .nest_service("/static", ServeDir::new(&static_dir))
            .layer(middleware::from_fn_with_state(
                data_dir.clone(),
                authenticate_middleware,
            ))
            .layer(CorsLayer::permissive());
        if rust_writer {
            observer = observer.route(
                "/internal/rust-writer/v2/operations",
                post(rust_writer_handler),
            );
        }
        observer
    } else {
        Router::new()
        .route("/health", get(health_handler))
        .route(
            "/internal/rust-writer/v2/operations",
            post(rust_writer_handler),
        )
        .route("/login", get(login_page_handler))
        .route("/api/login", post(login_handler))
        .route("/api/logout", post(logout_handler))
        .route("/api/terminal", get(list_terminals))
        .route("/api/terminal/start", post(start_terminal))
        .route("/api/terminal/{sid}/input", post(input_terminal))
        .route("/api/terminal/{sid}/drain", get(drain_terminal))
        .route("/api/terminal/{sid}/replay", get(replay_terminal))
        .route("/api/terminal/{sid}/resize", post(resize_terminal))
        .route("/api/terminal/{sid}/kill", post(kill_terminal))
        .route("/api/tasks", get(get_tasks_handler))
        .route("/api/worker-snapshots", get(worker_snapshots_handler))
        .route("/api/tasks/{id}", get(get_task_by_id_handler))
        .route("/api/task/{id}", get(get_task_by_id_handler))
        .route("/api/state", get(state_handler))
        .route(
            "/api/agent-hierarchy",
            get(agent_hierarchy_handler).post(agent_hierarchy_mutate_handler),
        )
        .route(
            "/api/controlplane/agent-hierarchy",
            get(agent_hierarchy_handler).post(agent_hierarchy_mutate_handler),
        )
        .route(
            "/api/agent-hierarchy/soul",
            get(hierarchy_soul_get_handler).post(hierarchy_soul_post_handler),
        )
        .route(
            "/api/agent-hierarchy/memory",
            get(hierarchy_memory_get_handler).post(hierarchy_memory_post_handler),
        )
        .route(
            "/api/agent-hierarchy/notebook",
            get(hierarchy_notebook_get_handler).post(hierarchy_notebook_post_handler),
        )
        .route(
            "/api/agent-hierarchy/toolsets",
            get(hierarchy_toolsets_handler),
        )
        .route(
            "/api/agent-hierarchy/routines",
            get(hierarchy_routines_get_handler).post(hierarchy_routines_post_handler),
        )
        .route(
            "/api/agent-hierarchy/routines/delete",
            post(hierarchy_routines_delete_handler),
        )
        .route(
            "/api/agent-hierarchy/shadows",
            get(hierarchy_shadows_handler).post(hierarchy_shadows_spawn_handler),
        )
        .route(
            "/api/agent-hierarchy/shadows/discard",
            post(hierarchy_shadows_discard_handler),
        )
        .route(
            "/api/agent-hierarchy/microapps",
            get(hierarchy_microapps_get_handler).post(hierarchy_microapps_post_handler),
        )
        .route(
            "/api/agent-hierarchy/microapps/delete",
            post(hierarchy_microapps_delete_handler),
        )
        .route("/api/agent-hierarchy/feed", get(hierarchy_feed_handler))
        .route(
            "/api/agent-hierarchy/command",
            post(hierarchy_command_handler),
        )
        .route(
            "/api/controlplane/agent-hierarchy/command",
            post(hierarchy_command_handler),
        )
        .route(
            "/api/agent-hierarchy/wiki/articles",
            get(hierarchy_wiki_articles_handler),
        )
        .route(
            "/api/agent-hierarchy/wiki/article",
            get(hierarchy_wiki_article_get_handler).post(hierarchy_wiki_article_post_handler),
        )
        .route("/api/overview", get(overview_handler))
        .route("/api/controlplane/overview", get(overview_handler))
        .route("/api/doc/search", get(doc_search_handler))
        .route("/api/rag/search", get(doc_search_handler))
        .route("/api/raggraph/index", post(raggraph_index_handler))
        .route("/api/raggraph/index-memories", post(raggraph_index_memories_handler))
        .route("/api/raggraph/query", post(raggraph_query_handler))
        .route("/api/raggraph/lineage/{session_id}", get(raggraph_lineage_handler))
        .route("/api/memory/vector-search", post(vector_search_handler))
        .route("/api/memory/vector-upsert", post(vector_upsert_handler))
        .route("/api/transport/inbound", post(transport_inbound_handler))
        .route("/api/events/ingest", post(event_ingest_handler))
        .route("/api/events/stream", get(event_stream_handler))
        .route("/api/context/hash", post(context_hash_handler))
        .route("/api/context/compact", post(context_compact_handler))
        .route("/api/worktree/spawn", post(worktree_spawn_handler))
        .route("/api/worktree/discard", post(worktree_discard_handler))
        .route("/api/civ/status", get(civ_status_handler))
        .route("/api/civ/bots", get(civ_bots_handler))
        .route("/api/civ/events", get(civ_events_handler))
        .route("/api/civ/resolve", post(civ_resolve_handler))
        .route("/api/civ/temporary-soul", post(civ_temporary_soul_handler))
        .route("/api/analysis/blast-radius", post(blast_radius_handler))
        .route("/api/tools/detect-loop", post(detect_loop_handler))
        .route(
            "/api/protocols/envelope/verify",
            post(protocol_envelope_verify_handler),
        )
        .route(
            "/api/protocols/bridge/translate",
            post(protocol_bridge_translate_handler),
        )
        .route("/api/kanban/claim", post(kanban_claim_handler))
        .route("/api/kanban/heartbeat", post(kanban_heartbeat_handler))
        .route("/api/code/symbols", post(code_symbols_handler))
        .route("/api/okf/scan", post(okf_scan_handler))
        .route("/api/sessions/fast", get(sessions_fast_handler))
        .route("/api/sessions/search", post(sessions_search_handler))
        .route("/api/fs/browse-fast", get(fs_browse_fast_handler))
        .route("/api/tools/search-files", post(tools_search_files_handler))
        .route("/api/tools/read-file", post(tools_read_file_handler))
        .route(
            "/api/subagent/spawn-headless",
            post(subagent_spawn_headless_handler),
        )
        .route("/v1/audio/transcriptions", post(stt_transcriptions_handler))
        .route(
            "/api/v1/audio/transcriptions",
            post(stt_transcriptions_handler),
        )
        .route("/api/timeline/fast", get(timeline_fast_handler))
        .route("/api/cancel/register", post(cancel_register_handler))
        .route("/api/cancel/trigger", post(cancel_trigger_handler))
        .route("/api/cancel/status", get(cancel_status_handler))
        .route(
            "/api/settings",
            get(get_settings_handler).post(post_settings_handler),
        )
        .route(
            "/api/system-one/decide",
            get(system_one_get_handler).post(system_one_post_handler),
        )
        .route("/api/system-one/list", get(system_one_list_handler))
        .route("/api/idempotency/check", post(idempotency_check_handler))
        .route("/api/idempotency/record", post(idempotency_record_handler))
        .route(
            "/api/response-store/{id}",
            get(response_store_get_handler).post(response_store_post_handler),
        )
        .route("/api/cron/executions", get(cron_executions_handler))
        .route(
            "/api/cron/deliveries/pending",
            get(cron_deliveries_pending_handler),
        )
        .route("/api/system-facts", get(system_facts_handler))
        .route("/api/agent-config", get(agent_config_handler))
        .route("/api/v1/models", get(models_handler))
        .route("/v1/models", get(models_handler))
        .route("/", get(index_handler))
        .route("/chat", get(index_handler))
        .route("/terminal", get(index_handler))
        .route("/taskboard", get(index_handler))
        .route("/scheduler", get(index_handler))
        .route("/events", get(index_handler))
        .route("/config", get(index_handler))
        .nest_service("/static", ServeDir::new(&static_dir))
        .fallback(proxy_fallback_handler)
        .layer(middleware::from_fn_with_state(
            data_dir.clone(),
            authenticate_middleware,
        ))
        .layer(CorsLayer::permissive())
    };

    let app = app.with_state(state);

    let resolved_host = resolve_bind_host(host);
    let addr: SocketAddr = format!("{resolved_host}:{port}")
        .parse()
        .map_err(|e| format!("Invalid bind address ({resolved_host}:{port}): {e}"))?;

    println!("============================================================");
    println!("🦀 HAOS Edge Rust Daemon online!");
    println!("   • Bind Address: http://{addr}/");
    println!("   • Static Dir:   {}", static_dir.display());
    if let Some(ref u) = upstream_url {
        println!("   • WebUI Fallback   -> {u}");
    }
    if let Some(ref g) = gateway_upstream_url {
        println!("   • Gateway Fallback -> {g}");
    }
    println!("   • Endpoints:    /health, /chat, /terminal, /api/terminal/*");
    println!("============================================================");

    // Watchdog em background para reabertura de locks de workers órfãos/mortos (Frente 3)
    if !observer_only {
        let watchdog_data_dir = data_dir.clone();
        tokio::spawn(async move {
            let kanban_db = watchdog_data_dir.join("kanban.db");
            let mut interval = tokio::time::interval(tokio::time::Duration::from_secs(5));
            loop {
                interval.tick().await;
                if kanban_db.exists() {
                    if let Ok(conn) = rusqlite::Connection::open_with_flags(
                        &kanban_db,
                        rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE
                            | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
                    ) {
                        let now = std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap_or_default()
                            .as_secs() as i64;
                        let _ = conn.execute(
                            "UPDATE tasks SET status = 'ready', claim_lock = NULL WHERE status = 'running' AND claim_expires IS NOT NULL AND claim_expires < ?1",
                            rusqlite::params![now - 30],
                        );
                    }
                }
            }
        });
    }

    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .map_err(|e| format!("Failed to bind TCP listener: {e}"))?;

    axum::serve(listener, app)
        .await
        .map_err(|e| format!("Server error: {e}"))?;

    Ok(())
}

async fn health_handler(State(state): State<AppState>) -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "contract_version": "haos-edge.readonly-sse.v1",
        "profile": state.profile,
        "schema_version": 1,
        "data": {
            "status": "healthy",
            "service": "haos-edge-rust",
            "runtime": "tokio+axum",
            "version": "0.1.0"
        }
    }))
}

/// The internal writer has an independent bearer-token boundary; WebUI cookies never authorize it.
async fn rust_writer_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(envelope): Json<serde_json::Value>,
) -> Response {
    let expected = match state.writer_token.as_deref() {
        Some(token) => token,
        None => return writer_error(StatusCode::FORBIDDEN, "forbidden"),
    };
    let expected_header = format!("Bearer {expected}");
    if headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        != Some(expected_header.as_str())
    {
        return writer_error(StatusCode::UNAUTHORIZED, "unauthenticated");
    }
    let executor = match state.writer_executor.as_deref() {
        Some(executor) => executor,
        None => return writer_error(StatusCode::FORBIDDEN, "forbidden"),
    };
    let binding = match WriterBinding::new(&state.profile, state.data_dir.to_string_lossy()) {
        Ok(binding) => binding,
        Err(_) => return writer_error(StatusCode::INTERNAL_SERVER_ERROR, "internal"),
    };
    let request = match crate::writer_envelope::validate_envelope(&envelope, &binding) {
        Ok(request) => request,
        Err(error) => return writer_error(StatusCode::BAD_REQUEST, error.code()),
    };
    match executor.execute(&request) {
        Ok(result) => (
            StatusCode::OK,
            Json(serde_json::json!({"ok": true, "result": result})),
        )
            .into_response(),
        Err(error) => writer_executor_error(error),
    }
}

fn writer_error(status: StatusCode, code: &str) -> Response {
    (
        status,
        Json(serde_json::json!({"ok": false, "error": {"code": code}})),
    )
        .into_response()
}

fn writer_executor_error(error: WriterError) -> Response {
    let (status, code) = match error {
        WriterError::InvalidBinding => (StatusCode::FORBIDDEN, "profile_mismatch"),
        WriterError::SchemaMismatch => (StatusCode::INTERNAL_SERVER_ERROR, "schema_mismatch"),
        WriterError::Timeout => (StatusCode::REQUEST_TIMEOUT, "timeout"),
        WriterError::Busy => (StatusCode::CONFLICT, "busy"),
        WriterError::NotFound => (StatusCode::NOT_FOUND, "not_found"),
        WriterError::Conflict(_) => (StatusCode::CONFLICT, "conflict"),
        WriterError::IdempotencyConflict => (StatusCode::CONFLICT, "idempotency_conflict"),
        WriterError::InvalidPayload => (StatusCode::BAD_REQUEST, "invalid_request"),
        WriterError::UnsupportedOperation(_) => (StatusCode::BAD_REQUEST, "unknown_operation"),
        WriterError::Storage(_) => (StatusCode::INTERNAL_SERVER_ERROR, "storage_unavailable"),
    };
    writer_error(status, code)
}

// ------------------------------------------------------------ auth
fn is_public_path(path: &str) -> bool {
    matches!(path, "/health" | "/login" | "/api/login" | "/api/logout")
        || path == "/static"
        || path.starts_with("/static/")
        || path.starts_with("/api/raggraph/")
        || path == "/api/raggraph/query"
        || path == "/api/raggraph/index"
        || path == "/api/raggraph/index-memories"
        || path == "/internal/rust-writer/v2/operations"
}

async fn authenticate_middleware(
    State(data_dir): State<PathBuf>,
    request: Request<axum::body::Body>,
    next: Next,
) -> Response {
    if is_public_path(request.uri().path())
        || auth::authenticate_request(&data_dir, request.headers()).is_ok()
    {
        next.run(request).await
    } else {
        unauthorized().into_response()
    }
}

fn cookie_from(headers: &HeaderMap) -> Option<&str> {
    headers.get(header::COOKIE).and_then(|v| v.to_str().ok())
}

fn unauthorized() -> (StatusCode, Json<serde_json::Value>) {
    (
        StatusCode::UNAUTHORIZED,
        Json(serde_json::json!({"error": "Não autenticado"})),
    )
}

async fn login_page_handler() -> impl IntoResponse {
    Html(auth::LOGIN_PAGE)
}

#[derive(Deserialize)]
pub struct LoginRequest {
    pub password: String,
    #[serde(default)]
    pub remember: bool,
}

async fn login_handler(
    State(state): State<AppState>,
    Json(payload): Json<LoginRequest>,
) -> impl IntoResponse {
    if !auth::password_is_set(&state.data_dir) {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({
                "error": format!(
                    "Senha não definida. Rode no nó: HAOS_DATA_DIR={} haos-edge admin set-password",
                    state.data_dir.display()
                )
            })),
        )
            .into_response();
    }
    if !auth::verify_password(&state.data_dir, &payload.password) {
        return (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"error": "Senha incorreta"})),
        )
            .into_response();
    }
    match auth::create_session(&state.data_dir, payload.remember) {
        Ok((_token, cookie)) => (
            [(header::SET_COOKIE, cookie)],
            Json(serde_json::json!({"ok": true})),
        )
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("Erro ao criar sessão: {e}"),
        )
            .into_response(),
    }
}

async fn logout_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    auth::destroy_session(&state.data_dir, cookie_from(&headers));
    (
        [(header::SET_COOKIE, auth::clear_cookie())],
        Json(serde_json::json!({"ok": true})),
    )
        .into_response()
}

async fn index_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return (StatusCode::FOUND, [(header::LOCATION, "/login")]).into_response();
    }
    let index_file = state.static_dir.join("index.html");
    if index_file.exists() {
        match std::fs::read_to_string(&index_file) {
            Ok(content) => Html(content).into_response(),
            Err(e) => (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Error reading index.html: {e}"),
            )
                .into_response(),
        }
    } else {
        Html("<h1>HAOS Edge Rust Server</h1><p>index.html not found</p>").into_response()
    }
}

async fn list_terminals(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let sessions = state.pty_manager.list();
    Json(serde_json::json!({ "sessions": sessions })).into_response()
}

async fn start_terminal(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<StartRequest>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    match state.pty_manager.start(payload.cwd.as_deref(), payload.env) {
        Ok(session) => Json(serde_json::json!({ "session_id": session.id, "pid": session.pid.as_raw(), "running": true })).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(serde_json::json!({ "error": e }))).into_response(),
    }
}

async fn input_terminal(
    AxPath(sid): AxPath<String>,
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<InputRequest>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let session = match state.pty_manager.get(&sid) {
        Some(s) => s,
        None => return StatusCode::NOT_FOUND.into_response(),
    };
    match session.write_input(payload.data.as_bytes()) {
        Ok(_) => Json(serde_json::json!({ "ok": true })).into_response(),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

async fn drain_terminal(
    AxPath(sid): AxPath<String>,
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let session = match state.pty_manager.get(&sid) {
        Some(s) => s,
        None => return StatusCode::NOT_FOUND.into_response(),
    };
    let (data, running) = session.drain();
    Json(DrainResponse {
        data,
        running,
        exit_code: if running { 0 } else { 1 },
    })
    .into_response()
}

async fn replay_terminal(
    AxPath(sid): AxPath<String>,
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let session = match state.pty_manager.get(&sid) {
        Some(s) => s,
        None => return StatusCode::NOT_FOUND.into_response(),
    };
    let (data, running) = session.get_replay();
    Json(serde_json::json!({
        "session_id": sid,
        "data": data,
        "buffer": data,
        "running": running,
        "exit_code": if running { 0 } else { 1 }
    }))
    .into_response()
}

async fn resize_terminal(
    AxPath(sid): AxPath<String>,
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<ResizeRequest>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let session = match state.pty_manager.get(&sid) {
        Some(s) => s,
        None => return StatusCode::NOT_FOUND.into_response(),
    };
    match session.resize(payload.rows, payload.cols) {
        Ok(_) => Json(serde_json::json!({ "ok": true })).into_response(),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

async fn kill_terminal(
    AxPath(sid): AxPath<String>,
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let ok = state.pty_manager.remove(&sid);
    Json(serde_json::json!({ "ok": ok })).into_response()
}

async fn get_task_by_id_handler(
    AxPath(task_id): AxPath<String>,
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    match DbHelper::get_task_details(&task_id) {
        Ok(details) => Json(details).into_response(),
        Err(e) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({ "error": e })),
        )
            .into_response(),
    }
}

async fn get_tasks_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    match DbHelper::get_tasks() {
        Ok(tasks) => Json(serde_json::json!({ "tasks": tasks })).into_response(),
        Err(e) => Json(serde_json::json!({ "error": e, "tasks": [] })).into_response(),
    }
}

async fn worker_snapshots_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<std::collections::HashMap<String, String>>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let profile = Some(state.profile.as_str());
    let now = query
        .get("now")
        .and_then(|value| value.parse::<i64>().ok())
        .unwrap_or_else(worker_snapshot::unix_now);
    let max_idle_seconds = query
        .get("max_idle_seconds")
        .and_then(|value| value.parse::<i64>().ok())
        .unwrap_or(30)
        .max(0);
    let db_path = DbHelper::get_haos_home().join("kanban.db");
    match worker_snapshot::read_snapshots(&db_path, profile, now, max_idle_seconds) {
        Ok(snapshots) => Json(serde_json::json!({
            "ok": true,
            "profile": state.profile,
            "db": db_path,
            "snapshots": snapshots,
        }))
        .into_response(),
        Err(error) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "ok": false, "db": db_path, "error": error.to_string() })),
        )
            .into_response(),
    }
}

async fn state_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let payload = DbHelper::get_state_payload(&state.data_dir);
    Json(payload).into_response()
}

#[derive(Deserialize)]
pub struct SearchQuery {
    pub q: String,
    pub limit: Option<usize>,
}

async fn doc_search_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    axum::extract::Query(query): axum::extract::Query<SearchQuery>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let limit = query.limit.unwrap_or(10).clamp(1, 100);
    match DbHelper::search_ragflow(&query.q, limit) {
        Ok(results) => {
            let items: Vec<serde_json::Value> = results
                .into_iter()
                .map(|(doc_path, header_path, anchor, content)| {
                    serde_json::json!({
                        "doc_path": doc_path,
                        "header_path": header_path,
                        "anchor": anchor,
                        "content": content,
                    })
                })
                .collect();
            Json(serde_json::json!({
                "query": query.q,
                "count": items.len(),
                "results": items,
            }))
            .into_response()
        }
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": err })),
        )
            .into_response(),
    }
}

#[derive(Deserialize)]
pub struct VectorSearchPayload {
    pub query_vector: Vec<f32>,
    pub model_version: String,
    pub limit: Option<usize>,
    pub db_path: Option<String>,
}

async fn vector_search_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<VectorSearchPayload>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }

    let limit = payload.limit.unwrap_or(20).clamp(1, 200);
    let db_path = if let Some(p) = payload.db_path {
        PathBuf::from(p)
    } else {
        state.data_dir.join("memory").join("vectors.db")
    };

    match crate::vector_search::NativeVectorEngine::search_vectors(
        &db_path,
        &payload.query_vector,
        &payload.model_version,
        limit,
    ) {
        Ok(results) => Json(serde_json::json!({
            "ok": true,
            "count": results.len(),
            "results": results,
            "engine": "rust_native_simd"
        }))
        .into_response(),
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "ok": false, "error": err })),
        )
            .into_response(),
    }
}

#[derive(Deserialize)]
pub struct VectorUpsertPayload {
    pub record_id: String,
    pub model_version: String,
    pub vector: Vec<f32>,
    pub db_path: Option<String>,
}

async fn vector_upsert_handler(
    State(state): State<AppState>,
    _headers: HeaderMap,
    Json(payload): Json<VectorUpsertPayload>,
) -> impl IntoResponse {
    let db_path = if let Some(p) = payload.db_path {
        PathBuf::from(p)
    } else {
        state.data_dir.join("memory").join("vectors.db")
    };

    match crate::vector_search::NativeVectorEngine::upsert_vector(
        &db_path,
        &payload.record_id,
        &payload.model_version,
        &payload.vector,
    ) {
        Ok(_) => Json(serde_json::json!({
            "ok": true,
            "record_id": payload.record_id,
            "dimensions": payload.vector.len(),
            "engine": "rust_native_sqlite_wal"
        }))
        .into_response(),
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "ok": false, "error": err })),
        )
            .into_response(),
    }
}

#[derive(Deserialize)]
pub struct RAGGraphIndexPayload {
    pub session_id: String,
}

#[derive(Deserialize)]
pub struct RAGGraphQueryPayload {
    pub query: String,
    pub k_hops: Option<usize>,
    pub limit: Option<usize>,
}

async fn raggraph_index_handler(
    State(state): State<AppState>,
    Json(payload): Json<RAGGraphIndexPayload>,
) -> impl IntoResponse {
    let haos_home = &state.data_dir;
    match crate::raggraph::RAGGraphEngine::index_session(haos_home, &payload.session_id) {
        Ok(nodes_indexed) => Json(serde_json::json!({
            "ok": true,
            "session_id": payload.session_id,
            "nodes_indexed": nodes_indexed,
            "engine": "rust_raggraph_sqlite"
        }))
        .into_response(),
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "ok": false, "error": err })),
        )
            .into_response(),
    }
}

async fn raggraph_index_memories_handler(
    State(state): State<AppState>,
) -> impl IntoResponse {
    let haos_home = &state.data_dir;
    match crate::raggraph::RAGGraphEngine::index_memories(haos_home) {
        Ok(memories_indexed) => Json(serde_json::json!({
            "ok": true,
            "memories_indexed": memories_indexed,
            "engine": "rust_raggraph_sqlite"
        }))
        .into_response(),
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "ok": false, "error": err })),
        )
            .into_response(),
    }
}

async fn raggraph_query_handler(
    State(state): State<AppState>,
    Json(payload): Json<RAGGraphQueryPayload>,
) -> impl IntoResponse {
    let haos_home = &state.data_dir;
    let k_hops = payload.k_hops.unwrap_or(2);
    let limit = payload.limit.unwrap_or(10);
    match crate::raggraph::RAGGraphEngine::query_raggraph(haos_home, &payload.query, k_hops, limit) {
        Ok(res) => Json(serde_json::json!({
            "ok": true,
            "result": res,
            "engine": "rust_raggraph_hybrid"
        }))
        .into_response(),
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "ok": false, "error": err })),
        )
            .into_response(),
    }
}

async fn raggraph_lineage_handler(
    State(state): State<AppState>,
    axum::extract::Path(session_id): axum::extract::Path<String>,
) -> impl IntoResponse {
    let haos_home = &state.data_dir;
    match crate::raggraph::RAGGraphEngine::get_session_lineage(haos_home, &session_id) {
        Ok(lineage) => Json(serde_json::json!({
            "ok": true,
            "lineage": lineage,
            "engine": "rust_raggraph_dag"
        }))
        .into_response(),
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "ok": false, "error": err })),
        )
            .into_response(),
    }
}


async fn transport_inbound_handler(
    State(state): State<AppState>,
    Json(payload): Json<crate::transport_ingress::InboundMessagePayload>,
) -> impl IntoResponse {
    match state.transport_ingress.ingest_inbound_message(payload) {
        Ok(_) => Json(serde_json::json!({ "ok": true, "transport": "rust_native_ingress" }))
            .into_response(),
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "ok": false, "error": err })),
        )
            .into_response(),
    }
}

// 1. Ingestão de Eventos via Rust Hub (Zero GIL / Batching Assíncrono)
async fn event_ingest_handler(
    State(state): State<AppState>,
    Json(event): Json<PlatformEvent>,
) -> impl IntoResponse {
    if state.observer_only {
        return (
            StatusCode::METHOD_NOT_ALLOWED,
            Json(serde_json::json!({
                "ok": false,
                "error": "observer_read_only",
                "contract_version": "haos-edge.readonly-sse.v1",
                "profile": state.profile,
            })),
        )
            .into_response();
    }
    match state.event_hub.publish(event) {
        Ok(_) => Json(serde_json::json!({ "ok": true, "ingested": true })).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "ok": false, "error": e })),
        )
            .into_response(),
    }
}

// 2. Stream SSE de Eventos em Tempo Real para WebUI / Dashboards
#[derive(Deserialize, Default)]
struct EventStreamQuery {
    last_seq: Option<i64>,
}

async fn event_stream_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<EventStreamQuery>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let last_seq = query.last_seq.unwrap_or(0);
    let mut rx = state.event_hub.sender.subscribe();
    let db_path = state.data_dir.join("events.db");
    let (replay, replay_limit_exceeded) = if last_seq > 0 {
        let mut events = Vec::new();
        let mut limited = false;
        if let Ok(conn) = rusqlite::Connection::open_with_flags(
            &db_path,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
        ) {
            let _ = conn.busy_timeout(Duration::from_secs(2));
            let _ = conn.execute_batch("PRAGMA query_only=ON;");
            if let Ok(mut stmt) = conn.prepare(
                "SELECT event_id, seq, name, trace_id, correlation_id, causation_id, trust_level, schema_version, timestamp, payload FROM events WHERE seq > ?1 ORDER BY seq LIMIT 101",
            ) {
                if let Ok(rows) = stmt.query_map([last_seq], |row| {
                    let raw: String = row.get(9)?;
                    let payload = serde_json::from_str(&raw).unwrap_or(serde_json::Value::Null);
                    Ok(PlatformEvent {
                        event_id: row.get(0)?,
                        seq: Some(row.get(1)?),
                        name: row.get(2)?,
                        trace_id: row.get(3)?,
                        correlation_id: row.get(4)?,
                        causation_id: row.get(5)?,
                        trust_level: row.get(6)?,
                        schema_version: row.get(7)?,
                        timestamp: row.get(8)?,
                        payload,
                    })
                }) {
                    for row in rows.flatten() {
                        events.push(row);
                    }
                }
            }
        }
        if events.len() > 100 {
            events.truncate(100);
            limited = true;
        }
        (events, limited)
    } else {
        (Vec::new(), false)
    };
    let stream = async_stream::stream! {
        let snapshot = serde_json::json!({
            "contract_version": "haos-edge.readonly-sse.v1",
            "profile": state.profile,
            "schema_version": 1,
            "data": {
                "type":"snapshot",
                "last_seq":last_seq
            }
        });
        yield Ok::<Event, axum::Error>(Event::default().event("snapshot").id(last_seq.to_string()).json_data(snapshot).unwrap());
        for row in replay {
            let id = row.seq.unwrap_or(0).to_string();
            let data = serde_json::json!({
                "contract_version": "haos-edge.readonly-sse.v1",
                "profile": state.profile,
                "schema_version": 1,
                "data": row,
            });
            yield Ok(Event::default().event("event").id(id).json_data(data).unwrap());
        }
        if replay_limit_exceeded {
            let data = serde_json::json!({
                "contract_version": "haos-edge.readonly-sse.v1",
                "profile": state.profile,
                "schema_version": 1,
                "data": {"reason": "replay_limit_exceeded"},
            });
            yield Ok(Event::default().event("resync").json_data(data).unwrap());
            return;
        }
        let mut heartbeat = tokio::time::interval(Duration::from_secs(15));
        loop {
            tokio::select! {
                result = rx.recv() => match result {
                    Ok(evt) => {
                        let id = evt.seq.unwrap_or(0).to_string();
                        let data = serde_json::json!({
                            "contract_version": "haos-edge.readonly-sse.v1",
                            "profile": state.profile,
                            "schema_version": 1,
                            "data": evt,
                        });
                        yield Ok(Event::default().event("event").id(id).json_data(data).unwrap());
                    },
                    Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => {
                        let data = serde_json::json!({
                            "contract_version": "haos-edge.readonly-sse.v1",
                            "profile": state.profile,
                            "schema_version": 1,
                            "data": {"reason": "stream_lagged"},
                        });
                        yield Ok(Event::default().event("resync").json_data(data).unwrap());
                        break;
                    },
                    Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
                },
                _ = heartbeat.tick() => yield Ok(Event::default().comment("heartbeat")),
            }
        }
    };
    let mut response = Sse::new(stream)
        .keep_alive(
            axum::response::sse::KeepAlive::new()
                .interval(Duration::from_secs(15))
                .text("heartbeat"),
        )
        .into_response();
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        axum::http::HeaderValue::from_static("text/event-stream"),
    );
    response.headers_mut().insert(
        header::CACHE_CONTROL,
        axum::http::HeaderValue::from_static("no-cache, no-transform"),
    );
    response.headers_mut().insert(
        header::HeaderName::from_static("x-accel-buffering"),
        axum::http::HeaderValue::from_static("no"),
    );
    response
}

// 3. Token & Context Hasher (SHA-256 SIMD / Rolling Prefixes)
#[derive(Deserialize)]
pub struct ContextHashPayload {
    pub text: String,
    pub segments: Option<usize>,
}

async fn context_hash_handler(Json(payload): Json<ContextHashPayload>) -> impl IntoResponse {
    let segments = payload.segments.unwrap_or(4);
    let fp = ContextHasher::compute_fingerprint(&payload.text, segments);
    Json(serde_json::json!({ "ok": true, "fingerprint": fp }))
}

async fn context_compact_handler(Json(payload): Json<CompactPayload>) -> impl IntoResponse {
    let res = ContextCompactor::compact(payload);
    Json(res)
}

// -------------------------------------------------------------
// Headless Subagents Handler (Tokio Tasks em Rust - Fase 3)
// -------------------------------------------------------------
async fn subagent_spawn_headless_handler(
    Json(payload): Json<crate::subagent_engine::HeadlessSubagentTask>,
) -> impl IntoResponse {
    let result = crate::subagent_engine::HeadlessRunner::spawn_task(payload).await;
    Json(result)
}

// -------------------------------------------------------------
// STT Handler Nativo em Rust (OpenAI-compatible /v1/audio/transcriptions)
// -------------------------------------------------------------
async fn stt_transcriptions_handler(
    headers: axum::http::HeaderMap,
    mut multipart: axum::extract::Multipart,
) -> impl IntoResponse {
    // 1. Validação de autenticação se configurado no ambiente
    let expected_token = std::env::var("HAOS_STT_TOKEN").unwrap_or_default();
    if !expected_token.is_empty() {
        let auth_header = headers
            .get("authorization")
            .and_then(|h| h.to_str().ok())
            .unwrap_or("");
        let expected_bearer = format!("Bearer {}", expected_token);
        if auth_header != expected_bearer {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({ "error": "invalid bearer token" })),
            )
                .into_response();
        }
    }

    let mut audio_bytes: Option<Vec<u8>> = None;
    let mut filename = "audio.ogg".to_string();
    let mut model = "large-v3".to_string();
    let mut language: Option<String> = None;
    let mut response_format = "json".to_string();

    while let Ok(Some(field)) = multipart.next_field().await {
        let name = field.name().unwrap_or("").to_string();
        if name == "file" {
            if let Some(fname) = field.file_name() {
                filename = fname.to_string();
            }
            if let Ok(bytes) = field.bytes().await {
                audio_bytes = Some(bytes.to_vec());
            }
        } else if name == "model" {
            if let Ok(text) = field.text().await {
                if !text.trim().is_empty() {
                    model = text.trim().to_string();
                }
            }
        } else if name == "language" {
            if let Ok(text) = field.text().await {
                if !text.trim().is_empty() {
                    language = Some(text.trim().to_string());
                }
            }
        } else if name == "response_format" {
            if let Ok(text) = field.text().await {
                response_format = text.trim().to_string();
            }
        }
    }

    let bytes = match audio_bytes {
        Some(b) if !b.is_empty() => b,
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({ "error": "empty or missing audio file" })),
            )
                .into_response();
        }
    };

    match crate::stt_engine::SttEngine::transcribe_audio(
        &bytes,
        &filename,
        &model,
        language.as_deref(),
    )
    .await
    {
        Ok(res) => {
            if response_format == "text" {
                res.text.into_response()
            } else {
                Json(res).into_response()
            }
        }
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": format!("transcription failed: {}", err) })),
        )
            .into_response(),
    }
}

// -------------------------------------------------------------
// System-One Fast-Path Endpoints (<0.1ms)
// -------------------------------------------------------------
#[derive(Deserialize)]
pub struct SystemOneGetQuery {
    pub key: String,
}

async fn system_one_get_handler(
    State(state): State<AppState>,
    Query(query): Query<SystemOneGetQuery>,
) -> impl IntoResponse {
    match state.system_one.get_decision(&query.key) {
        Some(rec) => Json(serde_json::json!({ "found": true, "decision": rec })).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({ "found": false })),
        )
            .into_response(),
    }
}

async fn system_one_post_handler(
    State(state): State<AppState>,
    Json(record): Json<DecisionRecord>,
) -> impl IntoResponse {
    match state.system_one.save_decision(&record) {
        Ok(_) => Json(serde_json::json!({ "ok": true, "saved": true })).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "ok": false, "error": e })),
        )
            .into_response(),
    }
}

#[derive(Deserialize)]
pub struct SystemOneListQuery {
    pub domain: Option<String>,
    pub limit: Option<usize>,
}

async fn system_one_list_handler(
    State(state): State<AppState>,
    Query(query): Query<SystemOneListQuery>,
) -> impl IntoResponse {
    let limit = query.limit.unwrap_or(50);
    let list = state
        .system_one
        .list_decisions(query.domain.as_deref(), limit);
    Json(serde_json::json!({ "decisions": list, "count": list.len() }))
}

// -------------------------------------------------------------
// Idempotency & Response Store (<0.2ms)
// -------------------------------------------------------------
#[derive(Deserialize)]
pub struct IdempotencyCheckPayload {
    pub scope: String,
    pub key: String,
}

async fn idempotency_check_handler(
    State(state): State<AppState>,
    Json(payload): Json<IdempotencyCheckPayload>,
) -> impl IntoResponse {
    match state
        .idempotency
        .check_idempotency(&payload.scope, &payload.key)
    {
        Some(rec) => Json(serde_json::json!({ "exists": true, "record": rec })).into_response(),
        None => Json(serde_json::json!({ "exists": false })).into_response(),
    }
}

#[derive(Deserialize)]
pub struct IdempotencyRecordPayload {
    pub scope: String,
    pub key: String,
    pub fingerprint: String,
    pub run_id: String,
    pub status: serde_json::Value,
}

async fn idempotency_record_handler(
    State(state): State<AppState>,
    Json(payload): Json<IdempotencyRecordPayload>,
) -> impl IntoResponse {
    match state.idempotency.record_idempotency(
        &payload.scope,
        &payload.key,
        &payload.fingerprint,
        &payload.run_id,
        &payload.status,
    ) {
        Ok(_) => Json(serde_json::json!({ "ok": true })).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "ok": false, "error": e })),
        )
            .into_response(),
    }
}

async fn response_store_get_handler(
    State(state): State<AppState>,
    AxPath(id): AxPath<String>,
) -> impl IntoResponse {
    match state.idempotency.get_response(&id) {
        Some(data) => Json(serde_json::json!({ "found": true, "data": data })).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({ "found": false })),
        )
            .into_response(),
    }
}

#[derive(Deserialize)]
pub struct ResponseStorePostPayload {
    pub data: String,
}

async fn response_store_post_handler(
    State(state): State<AppState>,
    AxPath(id): AxPath<String>,
    Json(payload): Json<ResponseStorePostPayload>,
) -> impl IntoResponse {
    match state.idempotency.save_response(&id, &payload.data) {
        Ok(_) => Json(serde_json::json!({ "ok": true })).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "ok": false, "error": e })),
        )
            .into_response(),
    }
}

// -------------------------------------------------------------
// Cron Executions & Delivery Ledger (<1ms)
// -------------------------------------------------------------
#[derive(Deserialize)]
pub struct CronExecutionsQuery {
    pub job_id: Option<String>,
    pub limit: Option<usize>,
}

async fn cron_executions_handler(
    State(state): State<AppState>,
    Query(query): Query<CronExecutionsQuery>,
) -> impl IntoResponse {
    let limit = query.limit.unwrap_or(50);
    let list = state
        .cron_ledger
        .list_executions(query.job_id.as_deref(), limit);
    Json(serde_json::json!({ "executions": list, "count": list.len() }))
}

#[derive(Deserialize)]
pub struct CronDeliveriesQuery {
    pub limit: Option<usize>,
}

async fn cron_deliveries_pending_handler(
    State(state): State<AppState>,
    Query(query): Query<CronDeliveriesQuery>,
) -> impl IntoResponse {
    let limit = query.limit.unwrap_or(50);
    let list = state.cron_ledger.list_pending_deliveries(limit);
    Json(serde_json::json!({ "pending_deliveries": list, "count": list.len() }))
}

// 4. Git Worktree Spawn & Discard em Rust Nativo
#[derive(Deserialize)]
pub struct WorktreeSpawnPayload {
    pub repo_dir: Option<String>,
    pub parent_bot_id: String,
    pub custom_leaf_id: Option<String>,
    pub base_commit: Option<String>,
}

async fn worktree_spawn_handler(
    State(_state): State<AppState>,
    Json(payload): Json<WorktreeSpawnPayload>,
) -> impl IntoResponse {
    let repo_dir = payload
        .repo_dir
        .map(PathBuf::from)
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_default());
    let shadows_root = PathBuf::from("/tmp/haos-shadows");
    let base_commit = payload.base_commit.as_deref().unwrap_or("HEAD");

    match NativeWorktreeEngine::spawn_worktree(
        &repo_dir,
        &shadows_root,
        &payload.parent_bot_id,
        payload.custom_leaf_id.as_deref(),
        base_commit,
    ) {
        Ok(info) => Json(serde_json::json!({ "ok": true, "worktree": info })).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "ok": false, "error": e })),
        )
            .into_response(),
    }
}

#[derive(Deserialize)]
pub struct WorktreeDiscardPayload {
    pub repo_dir: Option<String>,
    pub worktree_path: String,
    pub branch_name: Option<String>,
}

async fn worktree_discard_handler(
    Json(payload): Json<WorktreeDiscardPayload>,
) -> impl IntoResponse {
    let repo_dir = payload
        .repo_dir
        .map(PathBuf::from)
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_default());
    let wt_path = PathBuf::from(payload.worktree_path);

    match NativeWorktreeEngine::discard_worktree(
        &repo_dir,
        &wt_path,
        payload.branch_name.as_deref(),
    ) {
        Ok(_) => Json(serde_json::json!({ "ok": true, "discarded": true })).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "ok": false, "error": e })),
        )
            .into_response(),
    }
}

// Civilization Engine Native Endpoints
#[derive(Deserialize)]
pub struct CivResolvePayload {
    pub bot_id: String,
    pub root_dir: Option<String>,
    pub spec: Option<haos_civ::models::BotIdentitySpec>,
}

#[derive(Deserialize)]
pub struct CivTemporarySoulPayload {
    pub parent_soul: String,
    pub task_description: String,
    #[serde(default)]
    pub constraints: Vec<String>,
    pub council_context: Option<String>,
}

async fn civ_resolve_handler(Json(payload): Json<CivResolvePayload>) -> impl IntoResponse {
    let root = payload.root_dir.map(PathBuf::from).unwrap_or_else(|| {
        let home = std::env::var("HERMES_HOME")
            .or_else(|_| std::env::var("HOME"))
            .unwrap_or_else(|_| ".".to_string());
        PathBuf::from(home).join(".hermes").join("bots")
    });

    let resolver = haos_civ::IdentityResolver::new(&root);
    match resolver.resolve(&payload.bot_id, payload.spec.as_ref(), None) {
        Ok(bundle) => Json(serde_json::json!({ "ok": true, "bundle": bundle })).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
        )
            .into_response(),
    }
}

async fn civ_temporary_soul_handler(
    Json(payload): Json<CivTemporarySoulPayload>,
) -> impl IntoResponse {
    let soul = haos_civ::build_temporary_soul(
        &payload.parent_soul,
        &payload.task_description,
        &payload.constraints,
        payload.council_context.as_deref(),
    );
    let hash = haos_civ::compute_sha256(&soul);
    Json(serde_json::json!({
        "ok": true,
        "temporary_soul": soul,
        "temporary_soul_hash": hash,
    }))
    .into_response()
}

async fn civ_status_handler(
    State(state): State<AppState>,
) -> impl IntoResponse {
    let home = std::env::var("HERMES_HOME")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_else(|_| ".".to_string());
    let bots_dir = PathBuf::from(&home).join(".hermes").join("bots");

    let mut bots_count = 0;
    if let Ok(entries) = std::fs::read_dir(&bots_dir) {
        for entry in entries.flatten() {
            if entry.path().is_dir() {
                bots_count += 1;
            }
        }
    }

    let db_path = state.data_dir.join("events.db");
    let mut total_events = 0i64;
    let mut civ_events = 0i64;

    if let Ok(conn) = rusqlite::Connection::open_with_flags(
        &db_path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
    ) {
        if let Ok(cnt) = conn.query_row("SELECT COUNT(*) FROM events", [], |r| r.get(0)) {
            total_events = cnt;
        }
        if let Ok(cnt) = conn.query_row("SELECT COUNT(*) FROM events WHERE name LIKE 'civ.%'", [], |r| r.get(0)) {
            civ_events = cnt;
        }
    }

    Json(serde_json::json!({
        "ok": true,
        "engine": "haos-civ-native-rust",
        "accelerated_simd": true,
        "bots_count": bots_count,
        "total_events": total_events,
        "civ_events": civ_events,
        "bots_dir": bots_dir.display().to_string(),
        "database": db_path.display().to_string(),
    }))
}

async fn civ_bots_handler() -> impl IntoResponse {
    let home = std::env::var("HERMES_HOME")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_else(|_| ".".to_string());
    let bots_dir = PathBuf::from(&home).join(".hermes").join("bots");

    let mut bots = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&bots_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                let bot_id = entry.file_name().to_string_lossy().to_string();
                let has_soul = path.join("SOUL.md").exists();
                let has_identity = path.join("IDENTITY.md").exists();
                let has_values = path.join("VALUES.md").exists();

                let resolver = haos_civ::IdentityResolver::new(&bots_dir);
                let bundle_hash = match resolver.resolve(&bot_id, None, None) {
                    Ok(bundle) => bundle.bundle_hash,
                    Err(_) => String::new(),
                };

                bots.push(serde_json::json!({
                    "bot_id": bot_id,
                    "has_soul": has_soul,
                    "has_identity": has_identity,
                    "has_values": has_values,
                    "bundle_hash": bundle_hash,
                }));
            }
        }
    }

    Json(serde_json::json!({
        "ok": true,
        "bots_dir": bots_dir.display().to_string(),
        "bots": bots,
    }))
}

#[derive(Deserialize, Default)]
pub struct CivEventsQuery {
    pub limit: Option<usize>,
}

async fn civ_events_handler(
    State(state): State<AppState>,
    axum::extract::Query(query): axum::extract::Query<CivEventsQuery>,
) -> impl IntoResponse {
    let limit = query.limit.unwrap_or(50).min(500);
    let db_path = state.data_dir.join("events.db");
    let mut events = Vec::new();

    if let Ok(conn) = rusqlite::Connection::open_with_flags(
        &db_path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
    ) {
        if let Ok(mut stmt) = conn.prepare(
            "SELECT event_id, seq, name, trace_id, correlation_id, causation_id, trust_level, schema_version, timestamp, payload FROM events WHERE name LIKE 'civ.%' ORDER BY seq DESC LIMIT ?1",
        ) {
            if let Ok(rows) = stmt.query_map([limit], |row| {
                let raw: String = row.get(9)?;
                let payload = serde_json::from_str(&raw).unwrap_or(serde_json::Value::Null);
                Ok(serde_json::json!({
                    "event_id": row.get::<_, String>(0)?,
                    "seq": row.get::<_, i64>(1)?,
                    "name": row.get::<_, String>(2)?,
                    "trace_id": row.get::<_, Option<String>>(3)?,
                    "correlation_id": row.get::<_, Option<String>>(4)?,
                    "causation_id": row.get::<_, Option<String>>(5)?,
                    "trust_level": row.get::<_, String>(6)?,
                    "schema_version": row.get::<_, u32>(7)?,
                    "timestamp": row.get::<_, f64>(8)?,
                    "payload": payload,
                }))
            }) {
                for r in rows.flatten() {
                    events.push(r);
                }
            }
        }
    }

    Json(serde_json::json!({
        "ok": true,
        "count": events.len(),
        "events": events,
    }))
}


// 5. Blast Radius AST Analysis
#[derive(Deserialize)]
pub struct BlastRadiusPayload {
    pub root_dir: Option<String>,
    pub modified_files: Option<Vec<String>>,
    pub target_symbols: Option<Vec<String>>,
    pub max_depth: Option<usize>,
}

async fn blast_radius_handler(Json(payload): Json<BlastRadiusPayload>) -> impl IntoResponse {
    let root_dir = payload
        .root_dir
        .map(PathBuf::from)
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_default());
    let mod_files = payload.modified_files.unwrap_or_default();
    let symbols = payload.target_symbols.unwrap_or_default();
    let max_depth = payload.max_depth.unwrap_or(4);

    let result = FastAstAnalyzer::calculate_impact(&root_dir, &mod_files, &symbols, max_depth);
    Json(serde_json::json!({ "ok": true, "blast_radius": result }))
}

// 6. Loop Detector API (Anti-Infinite Loop de Ferramentas)
#[derive(Deserialize)]
pub struct DetectLoopPayload {
    pub session_id: String,
    pub tool_name: String,
    pub arguments: String,
    pub iteration: usize,
    pub threshold: Option<usize>,
}

async fn detect_loop_handler(
    State(state): State<AppState>,
    Json(payload): Json<DetectLoopPayload>,
) -> impl IntoResponse {
    let mut map = state.loop_detectors.lock().await;
    let threshold = payload.threshold.unwrap_or(3);
    let detector = map
        .entry(payload.session_id)
        .or_insert_with(|| LoopDetector::new(threshold, true));

    match detector.record_and_evaluate(&payload.tool_name, &payload.arguments, payload.iteration) {
        Some(alert) => {
            Json(serde_json::json!({ "ok": true, "loop_detected": true, "alert": alert }))
        }
        None => Json(serde_json::json!({ "ok": true, "loop_detected": false })),
    }
}

// 6.1 Native Protocol Fabric Endpoints (A2A, ACP, ANP, E2E)
async fn protocol_envelope_verify_handler(
    Json(envelope): Json<crate::protocols::ProtocolEnvelope>,
) -> impl IntoResponse {
    let valid = envelope.verify_signature();
    let canonical_hash = envelope.compute_canonical_hash();
    Json(serde_json::json!({
        "ok": true,
        "valid": valid,
        "envelope_id": envelope.envelope_id,
        "canonical_hash": canonical_hash,
        "trust_boundary": envelope.trust_boundary
    }))
}

#[derive(Deserialize)]
pub struct BridgeTranslatePayload {
    pub source_protocol: String,
    pub target_protocol: String,
    pub envelope: Option<crate::protocols::ProtocolEnvelope>,
    pub acp_event: Option<serde_json::Value>,
    pub sender: Option<String>,
    pub recipient: Option<String>,
}

async fn protocol_bridge_translate_handler(
    Json(payload): Json<BridgeTranslatePayload>,
) -> impl IntoResponse {
    let src = payload.source_protocol.to_uppercase();
    let tgt = payload.target_protocol.to_uppercase();
    let sender = payload.sender.as_deref().unwrap_or("haos_edge");
    let recipient = payload.recipient.as_deref().unwrap_or("haos_target");

    match (src.as_str(), tgt.as_str()) {
        ("ACP", "INTERNAL") => {
            let event = payload.acp_event.unwrap_or_else(|| serde_json::json!({}));
            let bridged = crate::protocols::FastCrossProtocolBridge::acp_to_internal(
                event, sender, recipient,
            );
            (
                StatusCode::OK,
                Json(serde_json::json!({ "ok": true, "envelope": bridged })),
            )
        }
        ("INTERNAL", "A2A") => {
            if let Some(env) = payload.envelope {
                let bridged = crate::protocols::FastCrossProtocolBridge::internal_to_a2a(&env);
                (
                    StatusCode::OK,
                    Json(serde_json::json!({ "ok": true, "envelope": bridged })),
                )
            } else {
                (
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({ "ok": false, "error": "Missing envelope" })),
                )
            }
        }
        ("A2A", "ANP") => {
            if let Some(env) = payload.envelope {
                let bridged =
                    crate::protocols::FastCrossProtocolBridge::a2a_to_anp(&env, sender, recipient);
                (
                    StatusCode::OK,
                    Json(serde_json::json!({ "ok": true, "envelope": bridged })),
                )
            } else {
                (
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({ "ok": false, "error": "Missing envelope" })),
                )
            }
        }
        _ => (
            StatusCode::NOT_IMPLEMENTED,
            Json(
                serde_json::json!({ "ok": false, "error": format!("Bridge translation from {} to {} not supported in fast path", src, tgt) }),
            ),
        ),
    }
}

// 7. Atomic Kanban Claim & Heartbeat Handlers (Frente 3 Ultra SOTA)
#[derive(Deserialize)]
pub struct KanbanClaimPayload {
    pub task_id: String,
    pub worker_id: Option<String>,
    pub ttl_seconds: Option<i64>,
    pub db_path: Option<String>,
}

async fn kanban_claim_handler(Json(payload): Json<KanbanClaimPayload>) -> impl IntoResponse {
    let db_path = payload.db_path.map(PathBuf::from).unwrap_or_else(|| {
        let home = std::env::var("HAOS_DATA_DIR")
            .or_else(|_| std::env::var("HERMES_HOME"))
            .unwrap_or_else(|_| "/root/.haos".to_string());
        PathBuf::from(home).join("kanban.db")
    });

    let Ok(conn) = rusqlite::Connection::open(&db_path) else {
        return Json(serde_json::json!({ "ok": false, "error": "db_open_failed" }));
    };

    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let ttl = payload.ttl_seconds.unwrap_or(300);
    let lease_expires = now + ttl;
    let claimer = payload
        .worker_id
        .unwrap_or_else(|| "haos-worker".to_string());

    // Update atômico: claim somente se READY, ou claim expirado
    let res = conn.execute(
        "UPDATE tasks SET status = 'RUNNING', claimer = ?1, claim_expires_at = ?2, started_at = coalesce(started_at, ?3) \
         WHERE (id = ?4 OR spec_id = ?4) AND (status = 'READY' OR (status = 'RUNNING' AND claim_expires_at < ?3))",
        rusqlite::params![claimer, lease_expires, now, payload.task_id],
    );

    match res {
        Ok(rows) if rows > 0 => Json(serde_json::json!({
            "ok": true,
            "claimed": true,
            "task_id": payload.task_id,
            "claimer": claimer,
            "lease_expires_at": lease_expires
        })),
        Ok(_) => Json(serde_json::json!({
            "ok": true,
            "claimed": false,
            "reason": "already_claimed_or_not_ready"
        })),
        Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
    }
}

#[derive(Deserialize)]
pub struct KanbanHeartbeatPayload {
    pub task_id: String,
    pub worker_id: Option<String>,
    pub ttl_seconds: Option<i64>,
    pub db_path: Option<String>,
}

async fn kanban_heartbeat_handler(
    Json(payload): Json<KanbanHeartbeatPayload>,
) -> impl IntoResponse {
    let db_path = payload.db_path.map(PathBuf::from).unwrap_or_else(|| {
        let home = std::env::var("HAOS_DATA_DIR")
            .or_else(|_| std::env::var("HERMES_HOME"))
            .unwrap_or_else(|_| "/root/.haos".to_string());
        PathBuf::from(home).join("kanban.db")
    });

    let Ok(conn) = rusqlite::Connection::open(&db_path) else {
        return Json(serde_json::json!({ "ok": false, "error": "db_open_failed" }));
    };

    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let ttl = payload.ttl_seconds.unwrap_or(300);
    let lease_expires = now + ttl;

    let res = if let Some(ref claimer) = payload.worker_id {
        conn.execute(
            "UPDATE tasks SET claim_expires_at = ?1 WHERE (id = ?2 OR spec_id = ?2) AND status = 'RUNNING' AND claimer = ?3",
            rusqlite::params![lease_expires, payload.task_id, claimer],
        )
    } else {
        conn.execute(
            "UPDATE tasks SET claim_expires_at = ?1 WHERE (id = ?2 OR spec_id = ?2) AND status = 'RUNNING'",
            rusqlite::params![lease_expires, payload.task_id],
        )
    };

    match res {
        Ok(rows) if rows > 0 => Json(
            serde_json::json!({ "ok": true, "renewed": true, "lease_expires_at": lease_expires }),
        ),
        Ok(_) => Json(
            serde_json::json!({ "ok": true, "renewed": false, "reason": "not_running_or_mismatched_worker" }),
        ),
        Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
    }
}

// 8. Fast Semantic Symbol & Call-Graph Parser (Frente 3)
#[derive(Deserialize)]
pub struct CodeSymbolsPayload {
    pub file_path: String,
    pub content: Option<String>,
}

#[derive(Serialize)]
pub struct SymbolDefinition {
    pub kind: String, // "class" | "def" | "const"
    pub name: String,
    pub line: usize,
}

async fn code_symbols_handler(Json(payload): Json<CodeSymbolsPayload>) -> impl IntoResponse {
    let content = match payload.content {
        Some(c) => c,
        None => match std::fs::read_to_string(&payload.file_path) {
            Ok(s) => s,
            Err(e) => {
                return Json(
                    serde_json::json!({ "ok": false, "error": format!("read_error: {e}") }),
                )
            }
        },
    };

    let mut symbols = Vec::new();
    for (idx, line) in content.lines().enumerate() {
        let trimmed = line.trim_start();
        if trimmed.starts_with("class ") {
            if let Some(rest) = trimmed.strip_prefix("class ") {
                let name = rest.split(&['(', ':'][..]).next().unwrap_or("").trim();
                if !name.is_empty() {
                    symbols.push(SymbolDefinition {
                        kind: "class".to_string(),
                        name: name.to_string(),
                        line: idx + 1,
                    });
                }
            }
        } else if trimmed.starts_with("def ") {
            if let Some(rest) = trimmed.strip_prefix("def ") {
                let name = rest.split('(').next().unwrap_or("").trim();
                if !name.is_empty() {
                    symbols.push(SymbolDefinition {
                        kind: "def".to_string(),
                        name: name.to_string(),
                        line: idx + 1,
                    });
                }
            }
        } else if trimmed.starts_with("pub struct ")
            || trimmed.starts_with("pub enum ")
            || trimmed.starts_with("pub fn ")
        {
            let parts: Vec<&str> = trimmed.split_whitespace().collect();
            if parts.len() >= 3 {
                let kind = parts[1].to_string();
                let name = parts[2]
                    .split(&['<', '(', '{', ';'][..])
                    .next()
                    .unwrap_or("")
                    .trim();
                symbols.push(SymbolDefinition {
                    kind,
                    name: name.to_string(),
                    line: idx + 1,
                });
            }
        } else if trimmed.starts_with("export function ") || trimmed.starts_with("function ") {
            let rest = if trimmed.starts_with("export function ") {
                &trimmed[16..]
            } else {
                &trimmed[9..]
            };
            let name = rest.split(&['(', '<'][..]).next().unwrap_or("").trim();
            if !name.is_empty() {
                symbols.push(SymbolDefinition {
                    kind: "function".to_string(),
                    name: name.to_string(),
                    line: idx + 1,
                });
            }
        } else if trimmed.starts_with("export const ") || trimmed.starts_with("const ") {
            let rest = if trimmed.starts_with("export const ") {
                &trimmed[13..]
            } else {
                &trimmed[6..]
            };
            if let Some((name_part, _)) = rest.split_once('=') {
                let name = name_part.split(':').next().unwrap_or("").trim();
                if !name.is_empty() && (rest.contains("=>") || rest.contains("function")) {
                    symbols.push(SymbolDefinition {
                        kind: "arrow_fn".to_string(),
                        name: name.to_string(),
                        line: idx + 1,
                    });
                }
            }
        } else if trimmed.starts_with("export interface ") || trimmed.starts_with("interface ") {
            let rest = if trimmed.starts_with("export interface ") {
                &trimmed[17..]
            } else {
                &trimmed[10..]
            };
            let name = rest.split(&['<', '{', ' '][..]).next().unwrap_or("").trim();
            if !name.is_empty() {
                symbols.push(SymbolDefinition {
                    kind: "interface".to_string(),
                    name: name.to_string(),
                    line: idx + 1,
                });
            }
        } else if trimmed.starts_with("func ") {
            let rest = &trimmed[5..];
            let name = rest.split('(').next().unwrap_or("").trim();
            if !name.is_empty() {
                symbols.push(SymbolDefinition {
                    kind: "func".to_string(),
                    name: name.to_string(),
                    line: idx + 1,
                });
            }
        }
    }

    Json(serde_json::json!({
        "ok": true,
        "file_path": payload.file_path,
        "symbols_count": symbols.len(),
        "symbols": symbols
    }))
}

// 11. Fast OKF v0.2 Knowledge Scanner & Indexer em Rust Nativo (Frente 1)
#[derive(Deserialize)]
pub struct OkfScanPayload {
    pub bundle_dir: String,
}

#[derive(Serialize)]
pub struct OkfScannedDoc {
    pub rel_path: String,
    pub title: String,
    pub tags: Vec<String>,
    pub body_preview: String,
}

async fn okf_scan_handler(Json(payload): Json<OkfScanPayload>) -> impl IntoResponse {
    let bundle_path = std::path::Path::new(&payload.bundle_dir);
    if !bundle_path.is_dir() {
        return Json(
            serde_json::json!({ "ok": false, "error": "bundle_dir_not_found", "docs": [] }),
        );
    }

    let mut docs = Vec::new();
    for entry in walkdir::WalkDir::new(bundle_path)
        .into_iter()
        .filter_map(|e| e.ok())
    {
        let p = entry.path();
        if p.extension().map_or(false, |ext| ext == "md") {
            if let Ok(content) = std::fs::read_to_string(p) {
                let rel = p
                    .strip_prefix(bundle_path)
                    .unwrap_or(p)
                    .to_string_lossy()
                    .replace('\\', "/");

                let mut title = rel.clone();
                let mut tags = Vec::new();
                let mut body = content.as_str();

                if content.starts_with("---") {
                    let parts: Vec<&str> = content.splitn(3, "---").collect();
                    if parts.len() >= 3 {
                        let frontmatter = parts[1];
                        body = parts[2].trim();

                        let mut in_tags_list = false;
                        for line in frontmatter.lines() {
                            let trimmed = line.trim();
                            if trimmed.starts_with("title:") {
                                in_tags_list = false;
                                title = trimmed[6..]
                                    .trim()
                                    .trim_matches('"')
                                    .trim_matches('\'')
                                    .to_string();
                            } else if trimmed.starts_with("tags:") {
                                let t_str = trimmed[5..].trim();
                                if t_str.starts_with('[') {
                                    in_tags_list = false;
                                    let cleaned = t_str.trim_matches('[').trim_matches(']');
                                    tags = cleaned
                                        .split(',')
                                        .map(|s| {
                                            s.trim()
                                                .trim_matches('"')
                                                .trim_matches('\'')
                                                .to_string()
                                        })
                                        .filter(|s| !s.is_empty())
                                        .collect();
                                } else {
                                    in_tags_list = true;
                                }
                            } else if in_tags_list {
                                if trimmed.starts_with('-') {
                                    let item =
                                        trimmed[1..].trim().trim_matches('"').trim_matches('\'');
                                    if !item.is_empty() {
                                        tags.push(item.to_string());
                                    }
                                } else if trimmed.contains(':') {
                                    in_tags_list = false;
                                }
                            }
                        }
                    }
                }

                let preview: String = body.chars().take(200).collect();
                docs.push(OkfScannedDoc {
                    rel_path: rel,
                    title,
                    tags,
                    body_preview: preview,
                });
            }
        }
    }

    Json(serde_json::json!({
        "ok": true,
        "bundle_dir": payload.bundle_dir,
        "count": docs.len(),
        "docs": docs
    }))
}

// 12. Fast Filesystem Browser em Rust Nativo (Frente 1)
#[derive(Serialize)]
pub struct FsItem {
    pub name: String,
    pub path: String,
    pub is_dir: bool,
    pub size_bytes: u64,
}

async fn fs_browse_fast_handler(
    Query(params): Query<std::collections::HashMap<String, String>>,
) -> impl IntoResponse {
    let raw_path = params.get("path").cloned().unwrap_or_default();
    let show_hidden = params
        .get("show_hidden")
        .map(|v| v == "1" || v == "true")
        .unwrap_or(false);

    let target_dir = if raw_path.trim().is_empty() {
        std::env::var("HOME")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|_| std::path::PathBuf::from("/root"))
    } else {
        std::path::PathBuf::from(raw_path)
    };

    let target_dir = if let Ok(canon) = target_dir.canonicalize() {
        if canon.is_dir() {
            canon
        } else {
            canon.parent().unwrap_or(&canon).to_path_buf()
        }
    } else {
        std::path::PathBuf::from("/root")
    };

    let mut items = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&target_dir) {
        for entry in entries.flatten() {
            let file_name = entry.file_name().to_string_lossy().to_string();
            if !show_hidden && file_name.starts_with('.') {
                continue;
            }
            let is_dir = entry.file_type().map(|ft| ft.is_dir()).unwrap_or(false);
            let size = entry.metadata().map(|m| m.len()).unwrap_or(0);
            let item_path = entry.path().to_string_lossy().to_string();

            items.push(FsItem {
                name: file_name,
                path: item_path,
                is_dir,
                size_bytes: size,
            });
        }
    }

    // Ordenação: Diretórios primeiro, depois alfabética
    items.sort_by(|a, b| {
        b.is_dir
            .cmp(&a.is_dir)
            .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
    });

    let current = target_dir.to_string_lossy().to_string();
    let parent = target_dir.parent().map(|p| p.to_string_lossy().to_string());

    Json(serde_json::json!({
        "ok": true,
        "current_path": current,
        "parent_path": parent,
        "count": items.len(),
        "items": items,
        "engine": "rust_fs_native"
    }))
}

// 12.1 Fast Tools Handlers: search-files e read-file via Rust Daemon (Opção 2)
#[derive(Deserialize)]
pub struct SearchFilesPayload {
    pub path: Option<String>,
    pub pattern: String,
    pub glob: Option<String>,
    pub max_matches: Option<usize>,
}

async fn tools_search_files_handler(Json(payload): Json<SearchFilesPayload>) -> impl IntoResponse {
    let root = payload
        .path
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| {
            std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."))
        });

    let max_matches = payload.max_matches.unwrap_or(250);

    match crate::file_engine::FastFileEngine::search_files(
        &root,
        &payload.pattern,
        payload.glob.as_deref(),
        max_matches,
    ) {
        Ok(res) => (
            StatusCode::OK,
            Json(serde_json::json!({ "ok": true, "result": res })),
        ),
        Err(err) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({ "ok": false, "error": err })),
        ),
    }
}

#[derive(Deserialize)]
pub struct ReadFilePayload {
    pub path: String,
    pub offset: Option<usize>,
    pub limit: Option<usize>,
}

async fn tools_read_file_handler(Json(payload): Json<ReadFilePayload>) -> impl IntoResponse {
    let path = std::path::PathBuf::from(payload.path);
    match crate::file_engine::FastFileEngine::read_file(&path, payload.offset, payload.limit) {
        Ok(res) => (
            StatusCode::OK,
            Json(serde_json::json!({ "ok": true, "result": res })),
        ),
        Err(err) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({ "ok": false, "error": err })),
        ),
    }
}

// 13. Fast Timeline & Event Aggregator em Rust Nativo (Frente 2)
async fn timeline_fast_handler(
    State(state): State<AppState>,
    Query(params): Query<std::collections::HashMap<String, String>>,
) -> impl IntoResponse {
    let limit: usize = params
        .get("limit")
        .and_then(|v| v.parse().ok())
        .unwrap_or(80);
    let events_db = state.data_dir.join("events.db");

    if !events_db.exists() {
        return Json(serde_json::json!({ "ok": true, "timeline": [], "count": 0 }));
    }

    let Ok(conn) = rusqlite::Connection::open_with_flags(
        &events_db,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
    ) else {
        return Json(serde_json::json!({ "ok": false, "error": "failed_open_events_db" }));
    };

    let mut stmt = match conn.prepare(
        "SELECT event_id, seq, name, trace_id, timestamp, payload \
         FROM events ORDER BY seq DESC LIMIT ?1",
    ) {
        Ok(s) => s,
        Err(e) => return Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
    };

    let rows = stmt.query_map([limit as i64], |row| {
        let event_id: Option<String> = row.get(0)?;
        let seq: i64 = row.get(1)?;
        let name: String = row.get(2)?;
        let trace_id: Option<String> = row.get(3)?;
        let timestamp: f64 = row.get(4)?;
        let payload_str: String = row.get(5)?;
        let payload: serde_json::Value =
            serde_json::from_str(&payload_str).unwrap_or(serde_json::Value::Null);

        Ok(serde_json::json!({
            "event_id": event_id,
            "seq": seq,
            "name": name,
            "trace_id": trace_id,
            "timestamp": timestamp,
            "payload": payload,
            "engine": "rust_timeline_aggregator"
        }))
    });

    let mut timeline = Vec::new();
    if let Ok(iter) = rows {
        for item in iter.flatten() {
            timeline.push(item);
        }
    }

    Json(serde_json::json!({
        "ok": true,
        "count": timeline.len(),
        "timeline": timeline
    }))
}

// 9. Fast Sessions Reader em Rust Nativo (state.db bypass de lock)
//
// Paridade com o fallback Python `standalone.py::_sessions_list`: agrega as DBs explícitas
// (`--sessions-db`) preservando a ordem, deduplica caminhos por realpath e IDs pela primeira
// ocorrência, aplica LIMIT 30 por banco, deriva título da primeira mensagem do usuário quando
// ausente, formata timestamps no fuso local e corta o resultado final em 40 itens.
//
// Campos extras da projeção (extraídos da branch residual sa-2-868eca1b / commit 4f4640f7b2,
// portados campo a campo sobre o handler atual sem regredir o envelope nem a agregação
// multi-DB): message_count, tool_call_count, last_active (frescor via MAX(messages.timestamp)
// combinado com last_activity_at/started_at), archived, pinned, parent_session_id,
// profile_name e cwd. Bancos legados sem essas colunas (ou sem a tabela messages com
// timestamp) caem na consulta canônica de 4 colunas com o shape antigo, mantendo a
// paridade diferencial com standalone.py — exatamente o contrato coberto por
// tests/haos_edge/test_readonly_sse_contract.py::test_session_parity_fixture_is_required.

/// Colunas extras da projeção rica; presente apenas quando o banco tem o schema
/// completo de `sessions` + `messages.timestamp` (hermes_state_common.py).
struct SessionsFastExtras {
    message_count: i64,
    tool_call_count: i64,
    last_active_raw: Option<f64>,
    archived: bool,
    pinned: bool,
    parent_session_id: Option<String>,
    profile_name: Option<String>,
    cwd: Option<String>,
}

/// Projeção rica alinhada ao schema canônico; o ORDER BY por DB permanece idêntico ao
/// canônico para não alterar a seleção LIMIT 30 nem a ordenação global por updated_ts.
const SESSIONS_FAST_RICH_SQL: &str = "SELECT s.id, s.title, s.started_at, s.last_activity_at, \
    s.message_count, s.tool_call_count, \
    MAX(COALESCE(s.last_activity_at, s.started_at), \
        COALESCE((SELECT MAX(m.timestamp) FROM messages m WHERE m.session_id = s.id), s.started_at)) AS last_active, \
    s.archived, s.pinned, s.parent_session_id, s.profile_name, s.cwd \
    FROM sessions s ORDER BY COALESCE(s.last_activity_at, s.started_at) DESC LIMIT ?1";

const SESSIONS_FAST_CANONICAL_SQL: &str =
    "SELECT id, title, started_at, last_activity_at FROM sessions \
     ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT ?1";

fn sessions_fast_db_rows(
    conn: &rusqlite::Connection,
    limit: i64,
) -> Vec<(
    String,
    Option<String>,
    Option<f64>,
    Option<f64>,
    Option<SessionsFastExtras>,
)> {
    if let Ok(mut stmt) = conn.prepare(SESSIONS_FAST_RICH_SQL) {
        let rows = stmt.query_map([limit], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<String>>(1)?,
                row.get::<_, Option<f64>>(2)?,
                row.get::<_, Option<f64>>(3)?,
                Some(SessionsFastExtras {
                    message_count: row.get::<_, Option<i64>>(4)?.unwrap_or(0),
                    tool_call_count: row.get::<_, Option<i64>>(5)?.unwrap_or(0),
                    last_active_raw: row.get::<_, Option<f64>>(6)?,
                    archived: row.get::<_, Option<i64>>(7)?.unwrap_or(0) != 0,
                    pinned: row.get::<_, Option<i64>>(8)?.unwrap_or(0) != 0,
                    parent_session_id: row.get::<_, Option<String>>(9)?,
                    profile_name: row.get::<_, Option<String>>(10)?,
                    cwd: row.get::<_, Option<String>>(11)?,
                }),
            ))
        });
        // SQLite só resolve nomes de coluna na EXECUÇÃO (prepare adia): um banco com
        // schema parcial (ex.: messages sem `timestamp`) prepara OK e falha em
        // query_map/step. Nesse caso cai na consulta canônica em vez de retornar vazio.
        if let Ok(iter) = rows {
            let collected: Vec<_> = iter.flatten().collect();
            if !collected.is_empty() {
                return collected;
            }
        }
    }
    // Schema legado: 4 colunas canônicas, sem campos extras (shape antigo preservado).
    let Ok(mut stmt) = conn.prepare(SESSIONS_FAST_CANONICAL_SQL) else {
        return Vec::new();
    };
    let rows = stmt.query_map([limit], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, Option<String>>(1)?,
            row.get::<_, Option<f64>>(2)?,
            row.get::<_, Option<f64>>(3)?,
            None,
        ))
    });
    match rows {
        Ok(iter) => iter.flatten().collect(),
        Err(_) => Vec::new(),
    }
}

fn format_local_timestamp(ts: Option<f64>) -> String {
    let Some(ts) = ts else { return String::new() };
    let secs = ts.floor() as i64;
    if secs == 0 {
        return String::new();
    }
    extern "C" {
        fn tzset();
        fn localtime_r(timep: *const libc::time_t, tm: *mut libc::tm) -> *mut libc::tm;
    }
    let raw = secs as libc::time_t;
    let mut tm_val: libc::tm = unsafe {
        tzset();
        std::mem::zeroed()
    };
    let ptr = unsafe { localtime_r(&raw, &mut tm_val) };
    if !ptr.is_null() {
        let y = tm_val.tm_year as i64 + 1900;
        let m = tm_val.tm_mon as i64 + 1;
        let d = tm_val.tm_mday as i64;
        let hh = tm_val.tm_hour as i64;
        let mm = tm_val.tm_min as i64;
        let ss = tm_val.tm_sec as i64;
        return format!("{y:04}-{m:02}-{d:02} {hh:02}:{mm:02}:{ss:02}");
    }
    String::new()
}

fn derive_title(raw_title: Option<String>, sid: &str, conn: &rusqlite::Connection) -> String {
    if let Some(ref title) = raw_title {
        if !title.trim().is_empty() {
            return title.clone();
        }
    }
    let first_msg: Option<String> = conn
        .prepare(
            "SELECT content FROM messages WHERE session_id = ?1 AND role = 'user' \
             ORDER BY id ASC LIMIT 1",
        )
        .and_then(|mut stmt| stmt.query_row([sid], |row| row.get::<_, Option<String>>(0)))
        .ok()
        .flatten();
    if let Some(content) = first_msg {
        let stripped = content.trim();
        if !stripped.is_empty() {
            let chars: Vec<char> = stripped.chars().collect();
            if chars.len() > 80 {
                let head: String = chars[..80].iter().collect();
                return format!("{head}…");
            }
            return stripped.to_owned();
        }
    }
    format!("Session {sid}")
}

#[cfg(test)]
mod sessions_fast_tests {
    use super::*;

    const RICH_SCHEMA: &str = "CREATE TABLE sessions (
        id TEXT, title TEXT, source TEXT, started_at REAL, last_activity_at REAL,
        message_count INTEGER, tool_call_count INTEGER, archived INTEGER, hidden INTEGER,
        pinned INTEGER, parent_session_id TEXT, profile_name TEXT, model TEXT, cwd TEXT
    );
    CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL);";

    fn conn_with(schema: &str, inserts: &[&str]) -> rusqlite::Connection {
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        conn.execute_batch(schema).unwrap();
        for sql in inserts {
            conn.execute_batch(sql).unwrap();
        }
        conn
    }

    #[test]
    fn rich_schema_returns_extras_with_real_last_active() {
        // last_activity_at=20 mas mensagem com timestamp=99: last_active deve ser o
        // frescor real (99), não o updated_ts (20).
        let conn = conn_with(
            RICH_SCHEMA,
            &[
                "INSERT INTO sessions VALUES ('s1','T','cli',10.0,20.0,7,3,0,0,1,'p1','prof','m','/work');",
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES ('s1','user','oi',99.0);",
            ],
        );
        let rows = sessions_fast_db_rows(&conn, 30);
        assert_eq!(rows.len(), 1);
        let (sid, _, _, _, extras) = &rows[0];
        assert_eq!(sid, "s1");
        let ex = extras.as_ref().expect("rich schema must produce extras");
        assert_eq!(ex.message_count, 7);
        assert_eq!(ex.tool_call_count, 3);
        assert!(!ex.archived);
        assert!(ex.pinned);
        assert_eq!(ex.parent_session_id.as_deref(), Some("p1"));
        assert_eq!(ex.profile_name.as_deref(), Some("prof"));
        assert_eq!(ex.cwd.as_deref(), Some("/work"));
        assert_eq!(ex.last_active_raw, Some(99.0));
    }

    #[test]
    fn rich_schema_without_messages_falls_back_to_activity() {
        let conn = conn_with(
            RICH_SCHEMA,
            &["INSERT INTO sessions VALUES ('s2',NULL,'cli',10.0,20.0,0,0,1,0,0,NULL,NULL,NULL,NULL);"],
        );
        let rows = sessions_fast_db_rows(&conn, 30);
        let ex = rows[0].4.as_ref().unwrap();
        // Sem mensagens: MAX(...) vira COALESCE(last_activity_at, started_at) = 20.
        assert_eq!(ex.last_active_raw, Some(20.0));
        assert!(ex.archived);
        assert!(!ex.pinned);
    }

    #[test]
    fn legacy_schema_without_extras_columns_falls_back_to_canonical() {
        // Schema legado (sem message_count/archived/...): prepare da consulta rica
        // falha e o handler mantém o shape antigo de 4 colunas, sem extras.
        let conn = conn_with(
            "CREATE TABLE sessions (id TEXT, title TEXT, started_at REAL, last_activity_at REAL);
             INSERT INTO sessions VALUES ('old','T',1.0,2.0);",
            &[],
        );
        let rows = sessions_fast_db_rows(&conn, 30);
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].0, "old");
        assert!(
            rows[0].4.is_none(),
            "legacy schema must not fabricate extras"
        );
    }

    #[test]
    fn partial_schema_messages_without_timestamp_falls_back_to_canonical() {
        // prepare() adia a resolução de nomes: messages sem `timestamp` prepara OK e
        // falha na execução — a consulta canônica precisa assumir o lugar.
        let conn = conn_with(
            "CREATE TABLE sessions (
                id TEXT, title TEXT, source TEXT, started_at REAL, last_activity_at REAL,
                message_count INTEGER, tool_call_count INTEGER, archived INTEGER, hidden INTEGER,
                pinned INTEGER, parent_session_id TEXT, profile_name TEXT, model TEXT, cwd TEXT);
             CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT);
             INSERT INTO sessions VALUES ('p1','T','cli',1.0,2.0,5,1,0,0,0,NULL,'x','m','/y');",
            &["INSERT INTO messages (session_id, role, content) VALUES ('p1','user','oi');"],
        );
        let rows = sessions_fast_db_rows(&conn, 30);
        assert_eq!(rows.len(), 1);
        assert!(
            rows[0].4.is_none(),
            "execution failure on rich query must degrade to canonical shape"
        );
    }
}

async fn sessions_fast_handler(
    State(state): State<AppState>,
    Query(params): Query<std::collections::HashMap<String, String>>,
) -> impl IntoResponse {
    let limit_per_db: usize = params
        .get("limit")
        .and_then(|v| v.parse().ok())
        .unwrap_or(30);

    // Candidate list mirrors the fallback's ordering: the profile primary DB first, then
    // explicit --sessions-db entries. Paths are deduped by canonical form and only existing
    // files participate.
    let mut candidates: Vec<PathBuf> = Vec::new();
    let mut seen_paths = std::collections::HashSet::new();
    for path in std::iter::once(&state.data_dir.join("state.db")).chain(state.sessions_dbs.iter()) {
        if !path.exists() {
            continue;
        }
        let resolved = path.canonicalize().unwrap_or_else(|_| path.clone());
        if seen_paths.insert(resolved) {
            candidates.push(path.clone());
        }
    }

    let mut results: Vec<(f64, serde_json::Value)> = Vec::new();
    let mut seen_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
    for db_file in &candidates {
        let Ok(conn) = rusqlite::Connection::open_with_flags(
            db_file,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
        ) else {
            continue;
        };
        let rows = sessions_fast_db_rows(&conn, limit_per_db as i64);
        for (sid, raw_title, started_raw, activity_raw, extras) in rows {
            if !seen_ids.insert(sid.clone()) {
                continue;
            }
            let started_ts = started_raw.unwrap_or(0.0);
            let updated_ts = activity_raw.filter(|v| *v != 0.0).unwrap_or(started_ts);
            let title = derive_title(raw_title, &sid, &conn);
            let mut row = serde_json::json!({
                "session_id": sid,
                "title": title,
                "started_at": format_local_timestamp(if started_ts != 0.0 { Some(started_ts) } else { None }),
                "updated_at": format_local_timestamp(if updated_ts != 0.0 { Some(updated_ts) } else { None }),
                "updated_ts": updated_ts,
                "db": db_file.display().to_string(),
            });
            if let Some(ex) = extras {
                // last_active: frescor real via MAX(messages.timestamp) combinado com
                // last_activity_at/started_at; cai para updated_ts quando o banco não tem
                // a coluna (mesma semântica do fallback Python em standalone.py).
                let last_active_ts = ex
                    .last_active_raw
                    .filter(|v| *v != 0.0)
                    .unwrap_or(updated_ts);
                row["message_count"] = serde_json::json!(ex.message_count);
                row["tool_call_count"] = serde_json::json!(ex.tool_call_count);
                row["last_active"] =
                    serde_json::json!(format_local_timestamp(if last_active_ts != 0.0 {
                        Some(last_active_ts)
                    } else {
                        None
                    }));
                row["last_active_ts"] = serde_json::json!(last_active_ts);
                row["archived"] = serde_json::json!(ex.archived);
                row["pinned"] = serde_json::json!(ex.pinned);
                row["parent_session_id"] = serde_json::json!(ex.parent_session_id);
                row["profile_name"] = serde_json::json!(ex.profile_name);
                row["cwd"] = serde_json::json!(ex.cwd);
            }
            results.push((updated_ts, row));
        }
    }

    // Stable sort descending by updated_ts, exactly like the reference's results.sort().
    results.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    let sessions: Vec<serde_json::Value> =
        results.into_iter().take(40).map(|(_, row)| row).collect();

    Json(serde_json::json!({
        "contract_version": "haos-edge.readonly-sse.v1",
        "profile": state.profile,
        "schema_version": 1,
        "data": {
            "count": sessions.len(),
            "sessions": sessions
        }
    }))
}

// 10. Fast FTS & Lexical Session Search em Rust Nativo (Frente 1)
#[derive(Deserialize)]
pub struct SessionSearchPayload {
    pub query: String,
    pub limit: Option<usize>,
}

async fn sessions_search_handler(
    State(state): State<AppState>,
    Json(payload): Json<SessionSearchPayload>,
) -> impl IntoResponse {
    let limit = payload.limit.unwrap_or(20);
    let state_db = state.data_dir.join("state.db");
    if !state_db.exists() {
        return Json(serde_json::json!({ "ok": true, "results": [], "count": 0 }));
    }

    let Ok(conn) = rusqlite::Connection::open_with_flags(
        &state_db,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
    ) else {
        return Json(serde_json::json!({ "ok": false, "error": "failed_open_state_db" }));
    };

    let q = payload.query.trim();
    if q.is_empty() {
        return Json(serde_json::json!({ "ok": true, "results": [], "count": 0 }));
    }

    let mut matches = Vec::new();

    // 1. Tenta via FTS5 index caso exista
    let fts_query = format!("\"{}\"*", q.replace('"', "\"\""));
    if let Ok(mut stmt) = conn.prepare(
        "SELECT s.id, s.title, snippet(messages_fts, -1, '<b>', '</b>', '...', 12) as snip \
         FROM messages_fts \
         JOIN messages m ON messages_fts.rowid = m.id \
         JOIN sessions s ON m.session_id = s.id \
         WHERE messages_fts MATCH ?1 \
         GROUP BY s.id \
         LIMIT ?2",
    ) {
        if let Ok(rows) = stmt.query_map(rusqlite::params![fts_query, limit as i64], |row| {
            let id: String = row.get(0)?;
            let title: Option<String> = row.get(1)?;
            let snippet: Option<String> = row.get(2)?;
            Ok(serde_json::json!({
                "session_id": id,
                "title": title.unwrap_or_else(|| "Sem título".to_string()),
                "snippet": snippet.unwrap_or_default(),
                "engine": "fts5_rust"
            }))
        }) {
            for m in rows.flatten() {
                matches.push(m);
            }
        }
    }

    // 2. Fallback index-free LIKE em Rust nativo se FTS5 falhou ou retornou vazio
    if matches.is_empty() {
        let pattern = format!("%{q}%");
        if let Ok(mut stmt) = conn.prepare(
            "SELECT id, title FROM sessions WHERE title LIKE ?1 ORDER BY started_at DESC LIMIT ?2",
        ) {
            if let Ok(rows) = stmt.query_map(rusqlite::params![pattern, limit as i64], |row| {
                let id: String = row.get(0)?;
                let title: Option<String> = row.get(1)?;
                Ok(serde_json::json!({
                    "session_id": id,
                    "title": title.unwrap_or_else(|| "Sem título".to_string()),
                    "snippet": "",
                    "engine": "like_fast_rust"
                }))
            }) {
                for m in rows.flatten() {
                    matches.push(m);
                }
            }
        }
    }

    Json(serde_json::json!({
        "ok": true,
        "count": matches.len(),
        "query": q,
        "results": matches
    }))
}

// 7. Cancel Registry API (Cancelamento Deterministico de Sombras e Subagentes)
#[derive(Deserialize)]
pub struct CancelPayload {
    pub id: String,
}

async fn cancel_register_handler(
    State(state): State<AppState>,
    Json(payload): Json<CancelPayload>,
) -> impl IntoResponse {
    let (tx, _rx) = tokio::sync::oneshot::channel();
    state.cancel_registry.register(payload.id.clone(), tx).await;
    Json(serde_json::json!({ "ok": true, "registered": true, "id": payload.id }))
}

async fn cancel_trigger_handler(
    State(state): State<AppState>,
    Json(payload): Json<CancelPayload>,
) -> impl IntoResponse {
    let success = state.cancel_registry.cancel(&payload.id).await;
    Json(serde_json::json!({ "ok": true, "cancelled": success, "id": payload.id }))
}

async fn cancel_status_handler(
    State(state): State<AppState>,
    axum::extract::Query(params): axum::extract::Query<HashMap<String, String>>,
) -> impl IntoResponse {
    let id = params.get("id").cloned().unwrap_or_default();
    let is_active = state.cancel_registry.is_active(&id).await;
    Json(serde_json::json!({ "ok": true, "id": id, "active": is_active }))
}

async fn get_settings_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let p = state.data_dir.join("settings.json");
    let val: serde_json::Value = if p.exists() {
        std::fs::read_to_string(&p)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| serde_json::json!({}))
    } else {
        serde_json::json!({ "max_global_concurrency": 8, "auto_dispatch": true })
    };
    Json(val).into_response()
}

async fn post_settings_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<serde_json::Value>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let p = state.data_dir.join("settings.json");
    let _ = std::fs::write(
        &p,
        serde_json::to_string_pretty(&payload).unwrap_or_default(),
    );
    Json(serde_json::json!({ "success": true, "settings": payload })).into_response()
}

async fn system_facts_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({
        "engine": "haos-edge-rust",
        "runtime": "tokio+axum",
        "data_dir": state.data_dir.display().to_string(),
        "models_suggestions": ["google/gemini-2.5-flash", "deepseek/deepseek-chat", "anthropic/claude-3-5-sonnet"],
        "note": "HAOS High-Performance Rust Control Plane"
    })).into_response()
}

async fn agent_config_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let haos_home = DbHelper::get_haos_home();
    let cfg_path = haos_home.join("config.yaml");
    let exists = cfg_path.exists();
    Json(serde_json::json!({
        "config_path": cfg_path.display().to_string(),
        "exists": exists,
        "managed": false,
        "sections": {}
    }))
    .into_response()
}

async fn models_handler() -> impl IntoResponse {
    Json(serde_json::json!({
        "object": "list",
        "data": [
            { "id": "google/gemini-2.5-flash", "object": "model", "owned_by": "haos" },
            { "id": "deepseek/deepseek-chat", "object": "model", "owned_by": "haos" },
            { "id": "anthropic/claude-3-5-sonnet", "object": "model", "owned_by": "haos" }
        ]
    }))
}

async fn proxy_fallback_handler(
    State(state): State<AppState>,
    req: axum::extract::Request,
) -> axum::response::Response {
    let raw_path = req.uri().path();
    let is_prefixed_gateway = raw_path.starts_with("/gateway/");
    let is_gateway_route = is_prefixed_gateway
        || raw_path.starts_with("/v1/")
        || raw_path.starts_with("/api/jobs")
        || raw_path.starts_with("/api/platforms/")
        || raw_path.starts_with("/api/cron/");

    let upstream_base = if is_gateway_route && state.gateway_upstream_url.is_some() {
        state
            .gateway_upstream_url
            .as_ref()
            .map(|u| u.trim_end_matches('/'))
    } else {
        state.upstream_url.as_ref().map(|u| u.trim_end_matches('/'))
    };

    let upstream = match upstream_base {
        Some(u) => u,
        None => {
            if !raw_path.starts_with("/api/")
                && !raw_path.starts_with("/v1/")
                && !is_prefixed_gateway
            {
                let index_file = state.static_dir.join("index.html");
                if index_file.exists() {
                    if let Ok(content) = std::fs::read_to_string(&index_file) {
                        return Html(content).into_response();
                    }
                }
            }
            return (StatusCode::NOT_FOUND, "Not found").into_response();
        }
    };

    let raw_pq = req
        .uri()
        .path_and_query()
        .map(|pq| pq.as_str())
        .unwrap_or(req.uri().path());

    let final_path = if is_prefixed_gateway {
        raw_pq.strip_prefix("/gateway").unwrap_or(raw_pq)
    } else {
        raw_pq
    };

    let target_url = format!("{upstream}{final_path}");

    let method = req.method().clone();
    let (parts, body) = req.into_parts();

    let reqwest_method =
        reqwest::Method::from_bytes(method.as_str().as_bytes()).unwrap_or(reqwest::Method::GET);
    let mut client_req = state.http_client.request(reqwest_method, &target_url);

    for (name, val) in parts.headers.iter() {
        if name != header::HOST {
            client_req = client_req.header(name.as_str(), val.as_bytes());
        }
    }

    let body_stream = body.into_data_stream();
    client_req = client_req.body(reqwest::Body::wrap_stream(body_stream));

    match client_req.send().await {
        Ok(upstream_resp) => {
            let status = upstream_resp.status();
            let headers = upstream_resp.headers().clone();

            let mut resp_builder = axum::response::Response::builder().status(status.as_u16());
            for (k, v) in headers.iter() {
                resp_builder = resp_builder.header(k.as_str(), v.as_bytes());
            }

            let stream = upstream_resp.bytes_stream();
            let body = axum::body::Body::from_stream(stream);

            resp_builder.body(body).unwrap_or_else(|_| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Failed to build response",
                )
                    .into_response()
            })
        }
        Err(err) => (
            StatusCode::BAD_GATEWAY,
            format!("HAOS Edge Proxy Error connecting to {target_url}: {err}"),
        )
            .into_response(),
    }
}

// =========================================================================
// AGENT HIERARCHY, TEAM GRAPH & CONTROL PLANE NATIVE HANDLERS EM RUST
// =========================================================================

async fn agent_hierarchy_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let p = state.data_dir.join("agent_hierarchy.json");
    if p.exists() {
        if let Ok(content) = std::fs::read_to_string(&p) {
            if let Ok(json_val) = serde_json::from_str::<serde_json::Value>(&content) {
                return Json(json_val).into_response();
            }
        }
    }
    Json(serde_json::json!({
        "version": "1.0",
        "nodes": [],
        "councils": [],
        "advisory_edges": [],
        "discussion_limits": {},
        "bot_model": {}
    }))
    .into_response()
}

async fn agent_hierarchy_mutate_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<serde_json::Value>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let p = state.data_dir.join("agent_hierarchy.json");
    if let Ok(bytes) = serde_json::to_vec_pretty(&payload) {
        if let Ok(_) = std::fs::write(&p, bytes) {
            return Json(serde_json::json!({ "ok": true, "saved": true })).into_response();
        }
    }
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({ "ok": false, "error": "failed_write" })),
    )
        .into_response()
}

async fn hierarchy_soul_get_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    axum::extract::Query(query): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let bot_id = query
        .get("bot_id")
        .cloned()
        .unwrap_or_else(|| "default".into());
    let soul_file = state.data_dir.join("souls").join(format!("{bot_id}.md"));
    let content = if soul_file.exists() {
        std::fs::read_to_string(&soul_file).unwrap_or_default()
    } else {
        String::new()
    };
    Json(serde_json::json!({ "bot_id": bot_id, "soul": content })).into_response()
}

async fn hierarchy_soul_post_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<serde_json::Value>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let bot_id = payload
        .get("bot_id")
        .and_then(|v| v.as_str())
        .unwrap_or("default");
    let content = payload.get("soul").and_then(|v| v.as_str()).unwrap_or("");
    let dir = state.data_dir.join("souls");
    let _ = std::fs::create_dir_all(&dir);
    let p = dir.join(format!("{bot_id}.md"));
    let _ = std::fs::write(p, content);
    Json(serde_json::json!({ "ok": true })).into_response()
}

async fn hierarchy_memory_get_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    axum::extract::Query(query): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let bot_id = query
        .get("bot_id")
        .cloned()
        .unwrap_or_else(|| "default".into());
    let mem_file = state
        .data_dir
        .join("memories")
        .join(format!("{bot_id}.json"));
    let val = if mem_file.exists() {
        std::fs::read_to_string(&mem_file)
            .ok()
            .and_then(|c| serde_json::from_str::<serde_json::Value>(&c).ok())
            .unwrap_or(serde_json::json!([]))
    } else {
        serde_json::json!([])
    };
    Json(serde_json::json!({ "bot_id": bot_id, "memories": val })).into_response()
}

async fn hierarchy_memory_post_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<serde_json::Value>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let bot_id = payload
        .get("bot_id")
        .and_then(|v| v.as_str())
        .unwrap_or("default");
    let dir = state.data_dir.join("memories");
    let _ = std::fs::create_dir_all(&dir);
    let p = dir.join(format!("{bot_id}.json"));
    let _ = std::fs::write(p, serde_json::to_vec_pretty(&payload).unwrap_or_default());
    Json(serde_json::json!({ "ok": true })).into_response()
}

async fn hierarchy_notebook_get_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    axum::extract::Query(query): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let bot_id = query
        .get("bot_id")
        .cloned()
        .unwrap_or_else(|| "default".into());
    let note_file = state
        .data_dir
        .join("notebooks")
        .join(format!("{bot_id}.md"));
    let content = if note_file.exists() {
        std::fs::read_to_string(&note_file).unwrap_or_default()
    } else {
        String::new()
    };
    Json(serde_json::json!({ "bot_id": bot_id, "notebook": content })).into_response()
}

async fn hierarchy_notebook_post_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<serde_json::Value>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let bot_id = payload
        .get("bot_id")
        .and_then(|v| v.as_str())
        .unwrap_or("default");
    let content = payload
        .get("notebook")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let dir = state.data_dir.join("notebooks");
    let _ = std::fs::create_dir_all(&dir);
    let p = dir.join(format!("{bot_id}.md"));
    let _ = std::fs::write(p, content);
    Json(serde_json::json!({ "ok": true })).into_response()
}

async fn hierarchy_toolsets_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({
        "toolsets": ["core", "terminal", "file", "web_search", "browser", "memory", "kanban", "skills"],
        "engine": "rust_native"
    })).into_response()
}

async fn hierarchy_routines_get_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let p = state.data_dir.join("routines.json");
    let val = if p.exists() {
        std::fs::read_to_string(&p)
            .ok()
            .and_then(|c| serde_json::from_str::<serde_json::Value>(&c).ok())
            .unwrap_or(serde_json::json!([]))
    } else {
        serde_json::json!([])
    };
    Json(val).into_response()
}

async fn hierarchy_routines_post_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<serde_json::Value>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let p = state.data_dir.join("routines.json");
    let _ = std::fs::write(p, serde_json::to_vec_pretty(&payload).unwrap_or_default());
    Json(serde_json::json!({ "ok": true })).into_response()
}

async fn hierarchy_routines_delete_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({ "ok": true })).into_response()
}

async fn hierarchy_shadows_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({ "shadows": [] })).into_response()
}

async fn hierarchy_shadows_spawn_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({ "ok": true, "status": "spawned" })).into_response()
}

async fn hierarchy_shadows_discard_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({ "ok": true, "status": "discarded" })).into_response()
}

async fn hierarchy_microapps_get_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({ "microapps": [] })).into_response()
}

async fn hierarchy_microapps_post_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({ "ok": true })).into_response()
}

async fn hierarchy_microapps_delete_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({ "ok": true })).into_response()
}

async fn hierarchy_feed_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    axum::extract::Query(query): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let target_id = query.get("target_id").cloned().unwrap_or_default();

    let mut tasks = Vec::new();
    let kanban_path = state.data_dir.join("kanban.db");
    if kanban_path.exists() {
        if let Ok(conn) = rusqlite::Connection::open_with_flags(
            &kanban_path,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
        ) {
            if !target_id.is_empty() {
                if let Ok(mut stmt) = conn.prepare("SELECT id, title, status, priority, assignee FROM tasks WHERE assignee = ?1 ORDER BY created_at DESC LIMIT 15;") {
                    if let Ok(rows) = stmt.query_map(rusqlite::params![target_id], |r| {
                        Ok(serde_json::json!({
                            "id": r.get::<_, String>(0)?,
                            "title": r.get::<_, String>(1)?,
                            "status": r.get::<_, String>(2)?,
                            "priority": r.get::<_, i32>(3)?,
                            "assignee": r.get::<_, Option<String>>(4)?
                        }))
                    }) {
                        for r in rows.flatten() {
                            tasks.push(r);
                        }
                    }
                }
            } else {
                if let Ok(mut stmt) = conn.prepare("SELECT id, title, status, priority, assignee FROM tasks ORDER BY created_at DESC LIMIT 15;") {
                    if let Ok(rows) = stmt.query_map([], |r| {
                        Ok(serde_json::json!({
                            "id": r.get::<_, String>(0)?,
                            "title": r.get::<_, String>(1)?,
                            "status": r.get::<_, String>(2)?,
                            "priority": r.get::<_, i32>(3)?,
                            "assignee": r.get::<_, Option<String>>(4)?
                        }))
                    }) {
                        for r in rows.flatten() {
                            tasks.push(r);
                        }
                    }
                }
            }
        }
    }

    Json(serde_json::json!({
        "ok": true,
        "target_id": target_id,
        "tasks": tasks,
        "events": []
    }))
    .into_response()
}

async fn hierarchy_wiki_articles_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let dir = state.data_dir.join("wiki");
    let mut articles = Vec::new();
    if dir.exists() {
        if let Ok(entries) = std::fs::read_dir(dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().and_then(|e| e.to_str()) == Some("md") {
                    let title = path
                        .file_stem()
                        .and_then(|s| s.to_str())
                        .unwrap_or("")
                        .to_string();
                    articles.push(
                        serde_json::json!({ "title": title, "path": path.display().to_string() }),
                    );
                }
            }
        }
    }
    Json(serde_json::json!({ "articles": articles })).into_response()
}

async fn hierarchy_wiki_article_get_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    axum::extract::Query(query): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let title = query.get("title").cloned().unwrap_or_default();
    let p = state.data_dir.join("wiki").join(format!("{title}.md"));
    let content = if p.exists() {
        std::fs::read_to_string(p).unwrap_or_default()
    } else {
        String::new()
    };
    Json(serde_json::json!({ "title": title, "content": content })).into_response()
}

async fn hierarchy_wiki_article_post_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<serde_json::Value>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let title = payload
        .get("title")
        .and_then(|v| v.as_str())
        .unwrap_or("Untitled");
    let content = payload
        .get("content")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let dir = state.data_dir.join("wiki");
    let _ = std::fs::create_dir_all(&dir);
    let p = dir.join(format!("{title}.md"));
    let _ = std::fs::write(p, content);
    Json(serde_json::json!({ "ok": true })).into_response()
}

async fn overview_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let state_payload = DbHelper::get_state_payload(&state.data_dir);
    Json(serde_json::json!({
        "system": "HAOS Native Edge",
        "runtime": "Rust Tokio + Axum",
        "state": state_payload
    }))
    .into_response()
}

async fn hierarchy_command_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<serde_json::Value>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let target_id = payload
        .get("target_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let command = payload
        .get("command")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if target_id.is_empty() || command.is_empty() {
        return Json(serde_json::json!({ "ok": false, "error": "target_id_and_command_required" }))
            .into_response();
    }

    let kanban_path = state.data_dir.join("kanban.db");
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;
    let task_id = format!("t_{:x}", now % 0xffffffff);
    let title = format!("Comando para {target_id}");

    if kanban_path.exists() {
        if let Ok(conn) = rusqlite::Connection::open(&kanban_path) {
            let _ = conn.execute(
                "INSERT INTO tasks (id, title, body, status, priority, assignee, created_at, started_at)
                 VALUES (?1, ?2, ?3, 'in_progress', 85, ?4, ?5, ?5)",
                rusqlite::params![task_id, title, command, target_id, now],
            );
        }
    }

    // Registra evento no events.db
    let events_path = state.data_dir.join("events.db");
    if events_path.exists() {
        if let Ok(conn) = rusqlite::Connection::open(&events_path) {
            let trace_id = format!("tr_{:x}", now);
            let p_str = serde_json::json!({
                "target_id": target_id,
                "command": command,
                "task_id": task_id
            })
            .to_string();
            let _ = conn.execute(
                "INSERT INTO events (name, timestamp, trace_id, payload) VALUES (?1, ?2, ?3, ?4)",
                rusqlite::params!["agent_hierarchy.commanded", now as f64, trace_id, p_str],
            );
        }
    }

    Json(serde_json::json!({
        "ok": true,
        "target_id": target_id,
        "task_id": task_id
    }))
    .into_response()
}
