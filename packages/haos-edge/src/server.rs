use crate::db::DbHelper;
use crate::pty::PtyManager;
use axum::extract::{Path as AxPath, State};
use axum::http::StatusCode;
use axum::response::{Html, IntoResponse, Json};
use axum::routing::{get, post};
use axum::Router;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::File;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;
use tower_http::cors::CorsLayer;
use tower_http::services::ServeDir;

#[derive(Clone)]
pub struct AppState {
    pub pty_manager: Arc<PtyManager>,
    pub static_dir: PathBuf,
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

pub async fn run_server(port: u16, host: &str, static_path: Option<PathBuf>) -> Result<(), String> {
    let data_dir = std::env::var("HAOS_DATA_DIR").unwrap_or_else(|_| "/tmp/haos_shared_data".into());
    let lock_path = Path::new(&data_dir).join(format!("controlplane_{port}.lock"));
    let _ = std::fs::create_dir_all(&data_dir);

    let lock_file = File::create(&lock_path).map_err(|e| format!("Failed to create lock file: {e}"))?;
    unsafe {
        let fd = std::os::fd::AsRawFd::as_raw_fd(&lock_file);
        if libc::flock(fd, libc::LOCK_EX | libc::LOCK_NB) != 0 {
            return Err(format!("Control plane is already running on port {port} (locked by another process)"));
        }
    }

    // Resolve static files directory
    let static_dir = if let Some(p) = static_path {
        p
    } else {
        let candidates = [
            PathBuf::from("hermes/platform/webui/static"),
            PathBuf::from("/usr/local/lib/haos-agent/hermes/platform/webui/static"),
            PathBuf::from("/run/media/adriano/e681b5ac-a4fb-44d4-aebf-9d6584065787/dsh-projetos/HERMES-TURBO/hermes/platform/webui/static"),
        ];
        candidates
            .into_iter()
            .find(|p| p.exists())
            .unwrap_or_else(|| PathBuf::from("static"))
    };

    let pty_manager = Arc::new(PtyManager::new());
    let state = AppState {
        pty_manager,
        static_dir: static_dir.clone(),
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
        .route("/api/terminal", get(list_terminals))
        .route("/api/terminal/start", post(start_terminal))
        .route("/api/terminal/{sid}/input", post(input_terminal))
        .route("/api/terminal/{sid}/drain", get(drain_terminal))
        .route("/api/terminal/{sid}/resize", post(resize_terminal))
        .route("/api/terminal/{sid}/kill", post(kill_terminal))
        .route("/api/tasks", get(get_tasks_handler))
        .route("/", get(index_handler))
        .route("/chat", get(index_handler))
        .route("/terminal", get(index_handler))
        .route("/taskboard", get(index_handler))
        .route("/scheduler", get(index_handler))
        .route("/events", get(index_handler))
        .route("/config", get(index_handler))
        .nest_service("/static", ServeDir::new(&static_dir))
        .layer(CorsLayer::permissive())
        .with_state(state);

    let addr: SocketAddr = format!("{host}:{port}")
        .parse()
        .map_err(|e| format!("Invalid bind address: {e}"))?;

    println!("============================================================");
    println!("🦀 HAOS Edge Rust Daemon online!");
    println!("   • Bind Address: http://{addr}/");
    println!("   • Static Dir:   {}", static_dir.display());
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

async fn index_handler(State(state): State<AppState>) -> impl IntoResponse {
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

async fn list_terminals(State(state): State<AppState>) -> Json<serde_json::Value> {
    let sessions = state.pty_manager.list();
    Json(serde_json::json!({ "sessions": sessions }))
}

async fn start_terminal(
    State(state): State<AppState>,
    Json(payload): Json<StartRequest>,
) -> Result<Json<serde_json::Value>, (StatusCode, String)> {
    let session = state
        .pty_manager
        .start(payload.cwd.as_deref(), payload.env)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e))?;

    Ok(Json(serde_json::json!({
        "session_id": session.id,
        "pid": session.pid.as_raw(),
        "running": true
    })))
}

async fn input_terminal(
    AxPath(sid): AxPath<String>,
    State(state): State<AppState>,
    Json(payload): Json<InputRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let session = state.pty_manager.get(&sid).ok_or(StatusCode::NOT_FOUND)?;
    session
        .write_input(payload.data.as_bytes())
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    Ok(Json(serde_json::json!({ "ok": true })))
}

async fn drain_terminal(
    AxPath(sid): AxPath<String>,
    State(state): State<AppState>,
) -> Result<Json<DrainResponse>, StatusCode> {
    let session = state.pty_manager.get(&sid).ok_or(StatusCode::NOT_FOUND)?;
    let (data, running) = session.drain();
    Ok(Json(DrainResponse {
        data,
        running,
        exit_code: if running { 0 } else { 1 },
    }))
}

async fn resize_terminal(
    AxPath(sid): AxPath<String>,
    State(state): State<AppState>,
    Json(payload): Json<ResizeRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let session = state.pty_manager.get(&sid).ok_or(StatusCode::NOT_FOUND)?;
    session
        .resize(payload.rows, payload.cols)
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    Ok(Json(serde_json::json!({ "ok": true })))
}

async fn kill_terminal(
    AxPath(sid): AxPath<String>,
    State(state): State<AppState>,
) -> Json<serde_json::Value> {
    let ok = state.pty_manager.remove(&sid);
    Json(serde_json::json!({ "ok": ok }))
}

async fn get_tasks_handler() -> Json<serde_json::Value> {
    match DbHelper::get_tasks() {
        Ok(tasks) => Json(serde_json::json!({ "tasks": tasks })),
        Err(e) => Json(serde_json::json!({ "error": e, "tasks": [] })),
    }
}
