use crate::auth;
use crate::db::DbHelper;
use crate::pty::PtyManager;
use axum::extract::{Path as AxPath, State};
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
    let state = AppState {
        pty_manager,
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
        .route("/api/terminal/{sid}/resize", post(resize_terminal))
        .route("/api/terminal/{sid}/kill", post(kill_terminal))
        .route("/api/tasks", get(get_tasks_handler))
        .route("/api/state", get(state_handler))
        .route("/api/doc/search", get(doc_search_handler))
        .route("/api/rag/search", get(doc_search_handler))
        .route("/api/settings", get(get_settings_handler).post(post_settings_handler))
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

    let addr: SocketAddr = format!("{host}:{port}")
        .parse()
        .map_err(|e| format!("Invalid bind address: {e}"))?;

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

