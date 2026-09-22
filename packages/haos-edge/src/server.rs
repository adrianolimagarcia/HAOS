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
use crate::worktree_engine::NativeWorktreeEngine;
use tokio::sync::Mutex as TokioMutex;
use axum::extract::{Path as AxPath, Query, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{Html, IntoResponse, Json};
use axum::routing::{get, post};
use axum::Router;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::File;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
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
    pub upstream_url: Option<String>,
    pub gateway_upstream_url: Option<String>,
    pub http_client: reqwest::Client,
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
    if trimmed.eq_ignore_ascii_case("auto") || trimmed.eq_ignore_ascii_case("tailnet") || trimmed.eq_ignore_ascii_case("tailscale") {
        // Nível 1: Tenta obter IP do Tailscale
        if let Ok(output) = std::process::Command::new("tailscale").args(["ip", "-4"]).output() {
            if output.status.success() {
                let ip_str = String::from_utf8_lossy(&output.stdout).trim().to_string();
                if !ip_str.is_empty() && ip_str.parse::<std::net::IpAddr>().is_ok() {
                    return ip_str;
                }
            }
        }
        if let Ok(output) = std::process::Command::new("ip").args(["-4", "addr", "show", "tailscale0"]).output() {
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
                            if !ipv4.is_loopback() && (ipv4.is_private() || ipv4.octets()[0] == 100) {
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
) -> Result<(), String> {
    let data_dir = PathBuf::from(
        std::env::var("HAOS_DATA_DIR").unwrap_or_else(|_| "/tmp/haos_shared_data".into()),
    );
    let lock_path = data_dir.join(format!("controlplane_{port}.lock"));
    let _ = std::fs::create_dir_all(&data_dir);

    let lock_file = File::create(&lock_path).map_err(|e| format!("Failed to create lock file: {e}"))?;
    unsafe {
        let fd = std::os::fd::AsRawFd::as_raw_fd(&lock_file);
        if libc::flock(fd, libc::LOCK_EX | libc::LOCK_NB) != 0 {
            return Err(format!("Control plane is already running on port {port} (locked by another process)"));
        }
    }
    auth::ensure_sessions_dir(&data_dir).map_err(|e| format!("Failed to init sessions dir: {e}"))?;

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

    let gateway_upstream_url = gateway_upstream
        .or_else(|| std::env::var("HAOS_UPSTREAM_GATEWAY").ok());

    let http_client = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(5))
        .build()
        .map_err(|e| format!("Failed to create reqwest client: {e}"))?;

    let pty_manager = Arc::new(PtyManager::new());
    let event_hub = Arc::new(EventHub::new(data_dir.join("events.db")));
    let cancel_registry = Arc::new(CancelRegistry::new());
    let loop_detectors = Arc::new(TokioMutex::new(HashMap::new()));
    let system_one = Arc::new(SystemOneEngine::new(&data_dir));
    let idempotency = Arc::new(IdempotencyEngine::new(&data_dir));
    let cron_ledger = Arc::new(CronLedgerEngine::new(&data_dir));
    let transport_ingress = Arc::new(crate::transport_ingress::TransportIngress::new((*event_hub).clone()));
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
        upstream_url: upstream_url.clone(),
        gateway_upstream_url: gateway_upstream_url.clone(),
        http_client,
    };

    // Background WAL auto-checkpoint thread every 5 minutes
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_secs(300)).await;
            DbHelper::checkpoint_all_dbs();
        }
    });

    let app = Router::new()
        .route("/health", get(health_handler))
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
        .route("/api/tasks/{id}", get(get_task_by_id_handler))
        .route("/api/task/{id}", get(get_task_by_id_handler))
        .route("/api/state", get(state_handler))
        .route("/api/agent-hierarchy", get(agent_hierarchy_handler).post(agent_hierarchy_mutate_handler))
        .route("/api/controlplane/agent-hierarchy", get(agent_hierarchy_handler).post(agent_hierarchy_mutate_handler))
        .route("/api/team-graph", get(team_graph_handler))
        .route("/api/controlplane/team_graph", get(team_graph_handler))
        .route("/api/agent-hierarchy/soul", get(hierarchy_soul_get_handler).post(hierarchy_soul_post_handler))
        .route("/api/agent-hierarchy/memory", get(hierarchy_memory_get_handler).post(hierarchy_memory_post_handler))
        .route("/api/agent-hierarchy/notebook", get(hierarchy_notebook_get_handler).post(hierarchy_notebook_post_handler))
        .route("/api/agent-hierarchy/toolsets", get(hierarchy_toolsets_handler))
        .route("/api/agent-hierarchy/routines", get(hierarchy_routines_get_handler).post(hierarchy_routines_post_handler))
        .route("/api/agent-hierarchy/routines/delete", post(hierarchy_routines_delete_handler))
        .route("/api/agent-hierarchy/shadows", get(hierarchy_shadows_handler).post(hierarchy_shadows_spawn_handler))
        .route("/api/agent-hierarchy/shadows/discard", post(hierarchy_shadows_discard_handler))
        .route("/api/agent-hierarchy/microapps", get(hierarchy_microapps_get_handler).post(hierarchy_microapps_post_handler))
        .route("/api/agent-hierarchy/microapps/delete", post(hierarchy_microapps_delete_handler))
        .route("/api/agent-hierarchy/feed", get(hierarchy_feed_handler))
        .route("/api/agent-hierarchy/wiki/articles", get(hierarchy_wiki_articles_handler))
        .route("/api/agent-hierarchy/wiki/article", get(hierarchy_wiki_article_get_handler).post(hierarchy_wiki_article_post_handler))
        .route("/api/harnesses", get(harnesses_handler))
        .route("/api/controlplane/harnesses", get(harnesses_handler))
        .route("/api/overview", get(overview_handler))
        .route("/api/controlplane/overview", get(overview_handler))
        .route("/api/doc/search", get(doc_search_handler))
        .route("/api/rag/search", get(doc_search_handler))
        .route("/api/memory/vector-search", post(vector_search_handler))
        .route("/api/memory/vector-upsert", post(vector_upsert_handler))
        .route("/api/transport/inbound", post(transport_inbound_handler))
        .route("/api/events/ingest", post(event_ingest_handler))
        .route("/api/events/stream", get(event_stream_handler))
        .route("/api/context/hash", post(context_hash_handler))
        .route("/api/context/compact", post(context_compact_handler))
        .route("/api/worktree/spawn", post(worktree_spawn_handler))
        .route("/api/worktree/discard", post(worktree_discard_handler))
        .route("/api/analysis/blast-radius", post(blast_radius_handler))
        .route("/api/tools/detect-loop", post(detect_loop_handler))
        .route("/api/protocols/envelope/verify", post(protocol_envelope_verify_handler))
        .route("/api/protocols/bridge/translate", post(protocol_bridge_translate_handler))
        .route("/api/kanban/claim", post(kanban_claim_handler))
        .route("/api/kanban/heartbeat", post(kanban_heartbeat_handler))
        .route("/api/code/symbols", post(code_symbols_handler))
        .route("/api/okf/scan", post(okf_scan_handler))
        .route("/api/sessions/fast", get(sessions_fast_handler))
        .route("/api/sessions/search", post(sessions_search_handler))
        .route("/api/fs/browse-fast", get(fs_browse_fast_handler))
        .route("/api/tools/search-files", post(tools_search_files_handler))
        .route("/api/tools/read-file", post(tools_read_file_handler))
        .route("/api/subagent/spawn-headless", post(subagent_spawn_headless_handler))
        .route("/v1/audio/transcriptions", post(stt_transcriptions_handler))
        .route("/api/v1/audio/transcriptions", post(stt_transcriptions_handler))
        .route("/api/timeline/fast", get(timeline_fast_handler))
        .route("/api/cancel/register", post(cancel_register_handler))
        .route("/api/cancel/trigger", post(cancel_trigger_handler))
        .route("/api/cancel/status", get(cancel_status_handler))
        .route("/api/settings", get(get_settings_handler).post(post_settings_handler))
        .route("/api/system-one/decide", get(system_one_get_handler).post(system_one_post_handler))
        .route("/api/system-one/list", get(system_one_list_handler))
        .route("/api/idempotency/check", post(idempotency_check_handler))
        .route("/api/idempotency/record", post(idempotency_record_handler))
        .route("/api/response-store/{id}", get(response_store_get_handler).post(response_store_post_handler))
        .route("/api/cron/executions", get(cron_executions_handler))
        .route("/api/cron/deliveries/pending", get(cron_deliveries_pending_handler))
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
        .layer(CorsLayer::permissive())
        .with_state(state);

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
    let watchdog_data_dir = data_dir.clone();
    tokio::spawn(async move {
        let kanban_db = watchdog_data_dir.join("kanban.db");
        let mut interval = tokio::time::interval(tokio::time::Duration::from_secs(5));
        loop {
            interval.tick().await;
            if kanban_db.exists() {
                if let Ok(conn) = rusqlite::Connection::open_with_flags(
                    &kanban_db,
                    rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
                ) {
                    let now = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_secs() as i64;
                    // Reseta tarefas em RUNNING cujo lease expirou há mais de 30s
                    let _ = conn.execute(
                        "UPDATE tasks SET status = 'ready', claim_lock = NULL WHERE status = 'running' AND claim_expires IS NOT NULL AND claim_expires < ?1",
                        rusqlite::params![now - 30],
                    );
                }
            }
        }
    });

    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .map_err(|e| format!("Failed to bind TCP listener: {e}"))?;

    axum::serve(listener, app)
        .await
        .map_err(|e| format!("Server error: {e}"))?;

    Ok(())
}

async fn health_handler() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "status": "healthy",
        "service": "haos-edge-rust",
        "runtime": "tokio+axum",
        "version": "0.1.0"
    }))
}

// ------------------------------------------------------------ auth
fn cookie_from(headers: &HeaderMap) -> Option<&str> {
    headers.get(header::COOKIE).and_then(|v| v.to_str().ok())
}

fn unauthorized() -> (StatusCode, Json<serde_json::Value>) {
    (StatusCode::UNAUTHORIZED, Json(serde_json::json!({"error": "Não autenticado"})))
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

async fn login_handler(State(state): State<AppState>, Json(payload): Json<LoginRequest>) -> impl IntoResponse {
    if !auth::password_is_set(&state.data_dir) {
        return (StatusCode::SERVICE_UNAVAILABLE, Json(serde_json::json!({
            "error": format!(
                "Senha não definida. Rode no nó: HAOS_DATA_DIR={} haos-edge admin set-password",
                state.data_dir.display()
            )
        }))).into_response();
    }
    if !auth::verify_password(&state.data_dir, &payload.password) {
        return (StatusCode::UNAUTHORIZED, Json(serde_json::json!({"error": "Senha incorreta"}))).into_response();
    }
    match auth::create_session(&state.data_dir, payload.remember) {
        Ok((_token, cookie)) => ([(header::SET_COOKIE, cookie)], Json(serde_json::json!({"ok": true}))).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, format!("Erro ao criar sessão: {e}")).into_response(),
    }
}

async fn logout_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    auth::destroy_session(&state.data_dir, cookie_from(&headers));
    ([(header::SET_COOKIE, auth::clear_cookie())], Json(serde_json::json!({"ok": true}))).into_response()
}

async fn index_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return (StatusCode::FOUND, [(header::LOCATION, "/login")]).into_response();
    }
    let index_file = state.static_dir.join("index.html");
    if index_file.exists() {
        match std::fs::read_to_string(&index_file) {
            Ok(content) => Html(content).into_response(),
            Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, format!("Error reading index.html: {e}")).into_response(),
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
    Json(DrainResponse { data, running, exit_code: if running { 0 } else { 1 } }).into_response()
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
    })).into_response()
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
        Err(e) => (StatusCode::NOT_FOUND, Json(serde_json::json!({ "error": e }))).into_response(),
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

async fn transport_inbound_handler(
    State(state): State<AppState>,
    Json(payload): Json<crate::transport_ingress::InboundMessagePayload>,
) -> impl IntoResponse {
    match state.transport_ingress.ingest_inbound_message(payload) {
        Ok(_) => Json(serde_json::json!({ "ok": true, "transport": "rust_native_ingress" })).into_response(),
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
async fn event_stream_handler(
    State(state): State<AppState>,
) -> impl IntoResponse {
    let mut rx = state.event_hub.sender.subscribe();
    let stream = async_stream::stream! {
        loop {
            if let Ok(evt) = rx.recv().await {
                if let Ok(json_str) = serde_json::to_string(&evt) {
                    yield Ok::<_, axum::Error>(format!("data: {json_str}\n\n"));
                }
            }
        }
    };
    axum::response::Response::builder()
        .header("Content-Type", "text/event-stream")
        .header("Cache-Control", "no-cache")
        .header("Connection", "keep-alive")
        .body(axum::body::Body::from_stream(stream))
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

// 3. Token & Context Hasher (SHA-256 SIMD / Rolling Prefixes)
#[derive(Deserialize)]
pub struct ContextHashPayload {
    pub text: String,
    pub segments: Option<usize>,
}

async fn context_hash_handler(
    Json(payload): Json<ContextHashPayload>,
) -> impl IntoResponse {
    let segments = payload.segments.unwrap_or(4);
    let fp = ContextHasher::compute_fingerprint(&payload.text, segments);
    Json(serde_json::json!({ "ok": true, "fingerprint": fp }))
}

async fn context_compact_handler(
    Json(payload): Json<CompactPayload>,
) -> impl IntoResponse {
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
        let auth_header = headers.get("authorization")
            .and_then(|h| h.to_str().ok())
            .unwrap_or("");
        let expected_bearer = format!("Bearer {}", expected_token);
        if auth_header != expected_bearer {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({ "error": "invalid bearer token" })),
            ).into_response();
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
            ).into_response();
        }
    };

    match crate::stt_engine::SttEngine::transcribe_audio(
        &bytes,
        &filename,
        &model,
        language.as_deref(),
    ).await {
        Ok(res) => {
            if response_format == "text" {
                res.text.into_response()
            } else {
                Json(res).into_response()
            }
        }
        Err(err) => {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({ "error": format!("transcription failed: {}", err) })),
            ).into_response()
        }
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
        None => (StatusCode::NOT_FOUND, Json(serde_json::json!({ "found": false }))).into_response(),
    }
}

async fn system_one_post_handler(
    State(state): State<AppState>,
    Json(record): Json<DecisionRecord>,
) -> impl IntoResponse {
    match state.system_one.save_decision(&record) {
        Ok(_) => Json(serde_json::json!({ "ok": true, "saved": true })).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(serde_json::json!({ "ok": false, "error": e }))).into_response(),
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
    let list = state.system_one.list_decisions(query.domain.as_deref(), limit);
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
    match state.idempotency.check_idempotency(&payload.scope, &payload.key) {
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
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(serde_json::json!({ "ok": false, "error": e }))).into_response(),
    }
}

async fn response_store_get_handler(
    State(state): State<AppState>,
    AxPath(id): AxPath<String>,
) -> impl IntoResponse {
    match state.idempotency.get_response(&id) {
        Some(data) => Json(serde_json::json!({ "found": true, "data": data })).into_response(),
        None => (StatusCode::NOT_FOUND, Json(serde_json::json!({ "found": false }))).into_response(),
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
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(serde_json::json!({ "ok": false, "error": e }))).into_response(),
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
    let list = state.cron_ledger.list_executions(query.job_id.as_deref(), limit);
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
    let repo_dir = payload.repo_dir.map(PathBuf::from).unwrap_or_else(|| std::env::current_dir().unwrap_or_default());
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
    let repo_dir = payload.repo_dir.map(PathBuf::from).unwrap_or_else(|| std::env::current_dir().unwrap_or_default());
    let wt_path = PathBuf::from(payload.worktree_path);

    match NativeWorktreeEngine::discard_worktree(&repo_dir, &wt_path, payload.branch_name.as_deref()) {
        Ok(_) => Json(serde_json::json!({ "ok": true, "discarded": true })).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "ok": false, "error": e })),
        )
            .into_response(),
    }
}

// 5. Blast Radius AST Analysis
#[derive(Deserialize)]
pub struct BlastRadiusPayload {
    pub root_dir: Option<String>,
    pub modified_files: Option<Vec<String>>,
    pub target_symbols: Option<Vec<String>>,
    pub max_depth: Option<usize>,
}

async fn blast_radius_handler(
    Json(payload): Json<BlastRadiusPayload>,
) -> impl IntoResponse {
    let root_dir = payload.root_dir.map(PathBuf::from).unwrap_or_else(|| std::env::current_dir().unwrap_or_default());
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
        Some(alert) => Json(serde_json::json!({ "ok": true, "loop_detected": true, "alert": alert })),
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
            let bridged = crate::protocols::FastCrossProtocolBridge::acp_to_internal(event, sender, recipient);
            (StatusCode::OK, Json(serde_json::json!({ "ok": true, "envelope": bridged })))
        }
        ("INTERNAL", "A2A") => {
            if let Some(env) = payload.envelope {
                let bridged = crate::protocols::FastCrossProtocolBridge::internal_to_a2a(&env);
                (StatusCode::OK, Json(serde_json::json!({ "ok": true, "envelope": bridged })))
            } else {
                (StatusCode::BAD_REQUEST, Json(serde_json::json!({ "ok": false, "error": "Missing envelope" })))
            }
        }
        ("A2A", "ANP") => {
            if let Some(env) = payload.envelope {
                let bridged = crate::protocols::FastCrossProtocolBridge::a2a_to_anp(&env, sender, recipient);
                (StatusCode::OK, Json(serde_json::json!({ "ok": true, "envelope": bridged })))
            } else {
                (StatusCode::BAD_REQUEST, Json(serde_json::json!({ "ok": false, "error": "Missing envelope" })))
            }
        }
        _ => (
            StatusCode::NOT_IMPLEMENTED,
            Json(serde_json::json!({ "ok": false, "error": format!("Bridge translation from {} to {} not supported in fast path", src, tgt) }))
        )
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

async fn kanban_claim_handler(
    Json(payload): Json<KanbanClaimPayload>,
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
    let claimer = payload.worker_id.unwrap_or_else(|| "haos-worker".to_string());

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
        Ok(rows) if rows > 0 => Json(serde_json::json!({ "ok": true, "renewed": true, "lease_expires_at": lease_expires })),
        Ok(_) => Json(serde_json::json!({ "ok": true, "renewed": false, "reason": "not_running_or_mismatched_worker" })),
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

async fn code_symbols_handler(
    Json(payload): Json<CodeSymbolsPayload>,
) -> impl IntoResponse {
    let content = match payload.content {
        Some(c) => c,
        None => {
            match std::fs::read_to_string(&payload.file_path) {
                Ok(s) => s,
                Err(e) => return Json(serde_json::json!({ "ok": false, "error": format!("read_error: {e}") })),
            }
        }
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
        } else if trimmed.starts_with("pub struct ") || trimmed.starts_with("pub enum ") || trimmed.starts_with("pub fn ") {
            let parts: Vec<&str> = trimmed.split_whitespace().collect();
            if parts.len() >= 3 {
                let kind = parts[1].to_string();
                let name = parts[2].split(&['<', '(', '{', ';'][..]).next().unwrap_or("").trim();
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

async fn okf_scan_handler(
    Json(payload): Json<OkfScanPayload>,
) -> impl IntoResponse {
    let bundle_path = std::path::Path::new(&payload.bundle_dir);
    if !bundle_path.is_dir() {
        return Json(serde_json::json!({ "ok": false, "error": "bundle_dir_not_found", "docs": [] }));
    }

    let mut docs = Vec::new();
    for entry in walkdir::WalkDir::new(bundle_path).into_iter().filter_map(|e| e.ok()) {
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

                        for line in frontmatter.lines() {
                            let trimmed = line.trim();
                            if trimmed.starts_with("title:") {
                                title = trimmed[6..].trim().trim_matches('"').trim_matches('\'').to_string();
                            } else if trimmed.starts_with("tags:") {
                                let t_str = trimmed[5..].trim().trim_matches('[').trim_matches(']');
                                tags = t_str
                                    .split(',')
                                    .map(|s| s.trim().trim_matches('"').trim_matches('\'').to_string())
                                    .filter(|s| !s.is_empty())
                                    .collect();
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
    let show_hidden = params.get("show_hidden").map(|v| v == "1" || v == "true").unwrap_or(false);

    let target_dir = if raw_path.trim().is_empty() {
        std::env::var("HOME").map(std::path::PathBuf::from).unwrap_or_else(|_| std::path::PathBuf::from("/root"))
    } else {
        std::path::PathBuf::from(raw_path)
    };

    let target_dir = if let Ok(canon) = target_dir.canonicalize() {
        if canon.is_dir() { canon } else { canon.parent().unwrap_or(&canon).to_path_buf() }
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
        b.is_dir.cmp(&a.is_dir).then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
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

async fn tools_search_files_handler(
    Json(payload): Json<SearchFilesPayload>,
) -> impl IntoResponse {
    let root = payload.path
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from(".")));

    let max_matches = payload.max_matches.unwrap_or(250);

    match crate::file_engine::FastFileEngine::search_files(&root, &payload.pattern, payload.glob.as_deref(), max_matches) {
        Ok(res) => (StatusCode::OK, Json(serde_json::json!({ "ok": true, "result": res }))),
        Err(err) => (StatusCode::BAD_REQUEST, Json(serde_json::json!({ "ok": false, "error": err }))),
    }
}

#[derive(Deserialize)]
pub struct ReadFilePayload {
    pub path: String,
    pub offset: Option<usize>,
    pub limit: Option<usize>,
}

async fn tools_read_file_handler(
    Json(payload): Json<ReadFilePayload>,
) -> impl IntoResponse {
    let path = std::path::PathBuf::from(payload.path);
    match crate::file_engine::FastFileEngine::read_file(&path, payload.offset, payload.limit) {
        Ok(res) => (StatusCode::OK, Json(serde_json::json!({ "ok": true, "result": res }))),
        Err(err) => (StatusCode::BAD_REQUEST, Json(serde_json::json!({ "ok": false, "error": err }))),
    }
}

// 13. Fast Timeline & Event Aggregator em Rust Nativo (Frente 2)
async fn timeline_fast_handler(
    State(state): State<AppState>,
    Query(params): Query<std::collections::HashMap<String, String>>,
) -> impl IntoResponse {
    let limit: usize = params.get("limit").and_then(|v| v.parse().ok()).unwrap_or(80);
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
        let payload: serde_json::Value = serde_json::from_str(&payload_str).unwrap_or(serde_json::Value::Null);

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
async fn sessions_fast_handler(
    State(state): State<AppState>,
    Query(params): Query<std::collections::HashMap<String, String>>,
) -> impl IntoResponse {
    let limit: usize = params
        .get("limit")
        .and_then(|v| v.parse().ok())
        .unwrap_or(30);

    let state_db = state.data_dir.join("state.db");
    if !state_db.exists() {
        return Json(serde_json::json!({ "ok": true, "sessions": [], "count": 0 }));
    }

    let Ok(conn) = rusqlite::Connection::open_with_flags(
        &state_db,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
    ) else {
        return Json(serde_json::json!({ "ok": false, "error": "failed_open_state_db" }));
    };

    let mut stmt = match conn.prepare(
        "SELECT id, title, started_at, last_activity_at FROM sessions \
         ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT ?1",
    ) {
        Ok(s) => s,
        Err(e) => return Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
    };

    let session_iter = stmt.query_map([limit as i64], |row| {
        let id: String = row.get(0)?;
        let title: Option<String> = row.get(1)?;
        let started_at: Option<f64> = row.get(2)?;
        let last_activity_at: Option<f64> = row.get(3)?;
        Ok(serde_json::json!({
            "id": id,
            "title": title.unwrap_or_else(|| "Sem título".to_string()),
            "started_at": started_at.unwrap_or(0.0),
            "last_activity_at": last_activity_at.unwrap_or(started_at.unwrap_or(0.0)),
        }))
    });

    let mut sessions = Vec::new();
    if let Ok(iter) = session_iter {
        for s in iter.flatten() {
            sessions.push(s);
        }
    }

    Json(serde_json::json!({
        "ok": true,
        "count": sessions.len(),
        "sessions": sessions
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

async fn get_settings_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
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
    let _ = std::fs::write(&p, serde_json::to_string_pretty(&payload).unwrap_or_default());
    Json(serde_json::json!({ "success": true, "settings": payload })).into_response()
}

async fn system_facts_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
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

async fn agent_config_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
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
    })).into_response()
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
        state.gateway_upstream_url.as_ref().map(|u| u.trim_end_matches('/'))
    } else {
        state.upstream_url.as_ref().map(|u| u.trim_end_matches('/'))
    };

    let upstream = match upstream_base {
        Some(u) => u,
        None => {
            if !raw_path.starts_with("/api/") && !raw_path.starts_with("/v1/") && !is_prefixed_gateway {
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

    let reqwest_method = reqwest::Method::from_bytes(method.as_str().as_bytes())
        .unwrap_or(reqwest::Method::GET);
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
                (StatusCode::INTERNAL_SERVER_ERROR, "Failed to build response").into_response()
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

async fn agent_hierarchy_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
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
    })).into_response()
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
    (StatusCode::INTERNAL_SERVER_ERROR, Json(serde_json::json!({ "ok": false, "error": "failed_write" }))).into_response()
}

async fn team_graph_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let p = state.data_dir.join("agent_hierarchy.json");
    let hierarchy = if p.exists() {
        std::fs::read_to_string(&p)
            .ok()
            .and_then(|c| serde_json::from_str::<serde_json::Value>(&c).ok())
            .unwrap_or(serde_json::json!({}))
    } else {
        serde_json::json!({})
    };

    let mut graph = serde_json::json!({
        "nodes": hierarchy.get("nodes").cloned().unwrap_or(serde_json::json!([])),
        "councils": hierarchy.get("councils").cloned().unwrap_or(serde_json::json!([])),
        "edges": hierarchy.get("advisory_edges").cloned().unwrap_or(serde_json::json!([])),
        "organizational_hierarchy": hierarchy,
        "engine": "haos-edge-rust-native"
    });
    Json(graph).into_response()
}

async fn hierarchy_soul_get_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    axum::extract::Query(query): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    let bot_id = query.get("bot_id").cloned().unwrap_or_else(|| "default".into());
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
    let bot_id = payload.get("bot_id").and_then(|v| v.as_str()).unwrap_or("default");
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
    let bot_id = query.get("bot_id").cloned().unwrap_or_else(|| "default".into());
    let mem_file = state.data_dir.join("memories").join(format!("{bot_id}.json"));
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
    let bot_id = payload.get("bot_id").and_then(|v| v.as_str()).unwrap_or("default");
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
    let bot_id = query.get("bot_id").cloned().unwrap_or_else(|| "default".into());
    let note_file = state.data_dir.join("notebooks").join(format!("{bot_id}.md"));
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
    let bot_id = payload.get("bot_id").and_then(|v| v.as_str()).unwrap_or("default");
    let content = payload.get("notebook").and_then(|v| v.as_str()).unwrap_or("");
    let dir = state.data_dir.join("notebooks");
    let _ = std::fs::create_dir_all(&dir);
    let p = dir.join(format!("{bot_id}.md"));
    let _ = std::fs::write(p, content);
    Json(serde_json::json!({ "ok": true })).into_response()
}

async fn hierarchy_toolsets_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({
        "toolsets": ["core", "terminal", "file", "web_search", "browser", "memory", "kanban", "skills"],
        "engine": "rust_native"
    })).into_response()
}

async fn hierarchy_routines_get_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
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

async fn hierarchy_routines_delete_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({ "ok": true })).into_response()
}

async fn hierarchy_shadows_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({ "shadows": [] })).into_response()
}

async fn hierarchy_shadows_spawn_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({ "ok": true, "status": "spawned" })).into_response()
}

async fn hierarchy_shadows_discard_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({ "ok": true, "status": "discarded" })).into_response()
}

async fn hierarchy_microapps_get_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({ "microapps": [] })).into_response()
}

async fn hierarchy_microapps_post_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({ "ok": true })).into_response()
}

async fn hierarchy_microapps_delete_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({ "ok": true })).into_response()
}

async fn hierarchy_feed_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({ "items": [] })).into_response()
}

async fn hierarchy_wiki_articles_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
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
                    let title = path.file_stem().and_then(|s| s.to_str()).unwrap_or("").to_string();
                    articles.push(serde_json::json!({ "title": title, "path": path.display().to_string() }));
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
    let title = payload.get("title").and_then(|v| v.as_str()).unwrap_or("Untitled");
    let content = payload.get("content").and_then(|v| v.as_str()).unwrap_or("");
    let dir = state.data_dir.join("wiki");
    let _ = std::fs::create_dir_all(&dir);
    let p = dir.join(format!("{title}.md"));
    let _ = std::fs::write(p, content);
    Json(serde_json::json!({ "ok": true })).into_response()
}

async fn harnesses_handler(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if !auth::session_valid(&state.data_dir, cookie_from(&headers)) {
        return unauthorized().into_response();
    }
    Json(serde_json::json!({
        "harnesses": [
            { "name": "local", "status": "active", "type": "process" },
            { "name": "haos-edge", "status": "active", "type": "rust-daemon" }
        ]
    })).into_response()
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
    })).into_response()
}
