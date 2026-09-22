pub mod proxy;
pub mod redact;
pub mod session_detail;
pub mod sessions;
pub mod status_cache;

use axum::{
    extract::{Query, Request, State},
    http::{header, StatusCode},
    response::{Html, IntoResponse, Json, Response},
    routing::get,
    Router,
};
use flate2::write::GzEncoder;
use flate2::Compression;
use session_detail::SessionQuery;
use sessions::{SessionCache, SessionManager};
use std::io::Write;
use std::net::SocketAddr;
use std::path::PathBuf;
use tower_http::cors::CorsLayer;
use tower_http::services::ServeDir;

pub fn resolve_bind_host(host_input: &str) -> String {
    let trimmed = host_input.trim();
    if trimmed.eq_ignore_ascii_case("auto") || trimmed.eq_ignore_ascii_case("tailnet") || trimmed.eq_ignore_ascii_case("tailscale") {
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
        return "127.0.0.1".to_string();
    }
    trimmed.to_string()
}

#[derive(Clone)]
pub struct AppState {
    pub backend_url: String,
    pub static_dir: PathBuf,
    pub haos_home: PathBuf,
    pub session_cache: SessionCache,
    pub status_cache: status_cache::SessionStatusCache,
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
    let status_cache = status_cache::SessionStatusCache::default();

    let http_client = reqwest::Client::builder()
        .tcp_nodelay(true)
        .pool_max_idle_per_host(32)
        .build()?;

    let state = AppState {
        backend_url: backend_url.clone(),
        static_dir: static_dir.clone(),
        haos_home,
        session_cache,
        status_cache,
        http_client,
    };

    println!("============================================================");
    println!("🦀 Hermes WebUI Edge (Rust Accelerator & Proxy) online!");
    println!("   • Public Bind:    http://{host}:{port}/");
    println!("   • Python Backend: {backend_url}");
    println!("   • Static Dir:     {}", static_dir.display());
    println!("   • Features:       Native Accelerated /api/session & /api/sessions, SIMD Redaction, Content-Negotiated GZIP");
    println!("============================================================");

    // Serviço nativo de arquivos estáticos em Rust com caching
    let serve_static = ServeDir::new(&static_dir)
        .append_index_html_on_directories(true);

    let app = Router::new()
        // 1. Endpoints de leitura acelerados em Rust Nativo (< 5ms)
        .route("/api/sessions", get(sessions_fast_handler))
        .route("/api/session", get(session_detail_handler))
        .route("/api/session/status", get(session_status_handler))
        .route("/health", get(health_handler))
        // 2. Arquivos estáticos servidos diretamente por Rust sem tocar no Python (Zero-Copy)
        .nest_service("/static", serve_static)
        .route("/", get(index_handler))
        // 3. Encaminhamento inteligente para o backend Python (SSE Streaming, Execução do AIAgent, POSTs)
        .fallback(proxy::proxy_handler)
        .layer(CorsLayer::permissive())
        .with_state(state);

    let addr: SocketAddr = format!("{}:{}", resolve_bind_host(&host), port).parse()?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}

async fn health_handler() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "status": "healthy",
        "service": "hermes-webui-edge",
        "engine": "rust-axum-tokio",
        "version": "0.2.0"
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

async fn session_detail_handler(
    State(state): State<AppState>,
    Query(query): Query<SessionQuery>,
    req: Request,
) -> Response {
    let accepts_gzip = req
        .headers()
        .get(header::ACCEPT_ENCODING)
        .and_then(|v| v.to_str().ok())
        .map(|enc| enc.contains("gzip"))
        .unwrap_or(false);

    match session_detail::load_and_prepare_session(&state.haos_home, &query) {
        Some(mut val) => {
            // Aplicar sanitização rápida de segredos em Rust
            redact::redact_value(&mut val);

            let json_bytes = serde_json::to_vec(&serde_json::json!({ "session": val }))
                .unwrap_or_default();

            // Compressão GZIP SOMENTE se o cliente suportar explicitamente Accept-Encoding: gzip
            if accepts_gzip && json_bytes.len() > 1024 {
                let mut encoder = GzEncoder::new(Vec::new(), Compression::fast());
                if encoder.write_all(&json_bytes).is_ok() {
                    if let Ok(compressed) = encoder.finish() {
                        return (
                            [
                                (header::CONTENT_TYPE, "application/json; charset=utf-8"),
                                (header::CONTENT_ENCODING, "gzip"),
                                (header::VARY, "Accept-Encoding"),
                            ],
                            compressed,
                        )
                            .into_response();
                    }
                }
            }

            (
                [
                    (header::CONTENT_TYPE, "application/json; charset=utf-8"),
                    (header::VARY, "Accept-Encoding"),
                ],
                json_bytes,
            )
                .into_response()
        }
        None => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({ "error": "session not found" })),
        )
            .into_response(),
    }
}

#[derive(serde::Deserialize)]
pub struct SessionStatusQuery {
    pub session_id: Option<String>,
}

async fn session_status_handler(
    State(state): State<AppState>,
    Query(query): Query<SessionStatusQuery>,
    _req: Request,
) -> Response {
    let session_id = query.session_id.unwrap_or_default();
    if !session_id.is_empty() {
        if let Some((body, _)) = state.status_cache.get(&session_id) {
            return (
                [
                    (header::CONTENT_TYPE, "application/json; charset=utf-8"),
                    (header::HeaderName::from_static("x-cache-edge"), "HIT"),
                ],
                body,
            )
                .into_response();
        }
    }

    // Se não estiver em cache, consulta o backend Python e popula o cache Rust
    let forward_url = format!("{}/api/session/status?session_id={session_id}", state.backend_url);
    match state.http_client.get(&forward_url).send().await {
        Ok(resp) => {
            let status_code = resp.status();
            let headers = resp.headers().clone();
            if let Ok(bytes) = resp.bytes().await {
                if status_code.is_success() && !session_id.is_empty() {
                    state.status_cache.insert(&session_id, bytes.to_vec(), vec![]);
                }

                let mut response = (status_code, bytes).into_response();
                for (k, v) in headers.iter() {
                    if k != header::CONTENT_LENGTH && k != header::TRANSFER_ENCODING {
                        response.headers_mut().insert(k.clone(), v.clone());
                    }
                }
                response.headers_mut().insert(
                    header::HeaderName::from_static("x-cache-edge"),
                    header::HeaderValue::from_static("MISS"),
                );
                response
            } else {
                StatusCode::BAD_GATEWAY.into_response()
            }
        }
        Err(_) => StatusCode::BAD_GATEWAY.into_response(),
    }
}
