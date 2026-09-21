mod proxy;
mod sessions;

use axum::{
    extract::State,
    http::{header, StatusCode},
    response::{Html, IntoResponse, Json},
    routing::get,
    Router,
};
use sessions::{SessionCache, SessionManager};
use std::net::SocketAddr;
use std::path::PathBuf;
use tower_http::cors::CorsLayer;
use tower_http::services::ServeDir;

#[derive(Clone)]
pub struct AppState {
    pub backend_url: String,
    pub static_dir: PathBuf,
    pub haos_home: PathBuf,
    pub session_cache: SessionCache,
    pub http_client: reqwest::Client,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt::init();

    let host = std::env::var("HERMES_WEBUI_EDGE_HOST").unwrap_or_else(|_| "100.77.31.78".to_string());
    let port = std::env::var("HERMES_WEBUI_EDGE_PORT").unwrap_or_else(|_| "8787".to_string());
    let backend_port = std::env::var("HERMES_WEBUI_PYTHON_PORT").unwrap_or_else(|_| "8786".to_string());
    let backend_url = format!("http://127.0.0.1:{backend_port}");

    let static_dir = std::env::var("HERMES_WEBUI_STATIC_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("/root/hermes-webui/static"));

    let haos_home = SessionManager::resolve_haos_home();
    let session_cache = SessionCache::new();

    let http_client = reqwest::Client::builder()
        .tcp_nodelay(true)
        .pool_max_idle_per_host(32)
        .build()?;

    let state = AppState {
        backend_url: backend_url.clone(),
        static_dir: static_dir.clone(),
        haos_home,
        session_cache,
        http_client,
    };

    println!("============================================================");
    println!("🦀 Hermes WebUI Edge (Rust Proxy & Accelerator) online!");
    println!("   • Public Bind:    http://{host}:{port}/");
    println!("   • Python Backend: {backend_url}");
    println!("   • Static Dir:     {}", static_dir.display());
    println!("   • Features:       Native Hybrid Sessions (SQLite + JSON), SSE Proxy, mmap Static Cache");
    println!("============================================================");

    // Serviço nativo de arquivos estáticos em Rust com caching
    let serve_static = ServeDir::new(&static_dir)
        .append_index_html_on_directories(true);

    let app = Router::new()
        // 1. Endpoints de leitura acelerados em Rust Nativo (< 1ms)
        .route("/api/sessions", get(sessions_fast_handler))
        .route("/health", get(health_handler))
        // 2. Arquivos estáticos servidos diretamente por Rust sem tocar no Python (Zero-Copy)
        .nest_service("/static", serve_static)
        .route("/", get(index_handler))
        // 3. Encaminhamento inteligente para o backend Python (SSE Streaming, Execução do AIAgent)
        .fallback(proxy::proxy_handler)
        .layer(CorsLayer::permissive())
        .with_state(state);

    let addr: SocketAddr = format!("{host}:{port}").parse()?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}

async fn health_handler() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "status": "healthy",
        "service": "hermes-webui-edge",
        "engine": "rust-axum-tokio",
        "version": "0.1.0"
    }))
}

async fn index_handler(State(state): State<AppState>) -> impl IntoResponse {
    let index_file = state.static_dir.join("index.html");
    if index_file.exists() {
        if let Ok(content) = std::fs::read_to_string(&index_file) {
            return ([(header::CONTENT_TYPE, "text/html; charset=utf-8")], Html(content)).into_response();
        }
    }
    (StatusCode::NOT_FOUND, "index.html not found").into_response()
}

async fn sessions_fast_handler(State(state): State<AppState>) -> impl IntoResponse {
    let sessions = state.session_cache.get_or_refresh(&state.haos_home);
    Json(serde_json::json!({ "sessions": sessions }))
}
