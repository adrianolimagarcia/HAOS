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
use tower_http::cors::Any;
use tower_http::cors::CorsLayer;
use tower_http::services::ServeDir;

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

    // Bind locally unless an operator explicitly opts into a network address.
    // The old default exposed the service on a machine-specific tailnet IP.
    let host = std::env::var("HERMES_WEBUI_EDGE_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
    let port = std::env::var("HERMES_WEBUI_EDGE_PORT").unwrap_or_else(|_| "8787".to_string());
    let backend_port =
        std::env::var("HERMES_WEBUI_PYTHON_PORT").unwrap_or_else(|_| "8786".to_string());
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
    let serve_static = ServeDir::new(&static_dir).append_index_html_on_directories(true);

    // Same-origin is the safe default.  Cross-origin access must be explicitly
    // configured with one exact origin; wildcard CORS would expose session data.
    let cors = match std::env::var("HERMES_WEBUI_EDGE_CORS_ORIGIN") {
        Ok(origin) if !origin.trim().is_empty() => CorsLayer::new()
            .allow_origin(origin.parse::<header::HeaderValue>()?)
            .allow_methods(Any)
            .allow_headers(Any),
        _ => CorsLayer::new(),
    };

    let app = Router::new()
        // 1. Endpoints de leitura acelerados em Rust Nativo (< 5ms)
        .route("/api/sessions", get(sessions_fast_handler))
        .route("/api/session", get(session_detail_handler))
        .route("/api/session/status", get(session_status_handler))
        .route("/api/raggraph/{*path}", get(raggraph_proxy_handler).post(raggraph_proxy_handler))
        .route("/api/events", get(proxy::events_ws_handler))
        .route("/health", get(health_handler))
        // 2. Arquivos estáticos servidos diretamente por Rust sem tocar no Python (Zero-Copy)
        .nest_service("/static", serve_static)
        .route("/", get(index_handler))
        // 3. Encaminhamento inteligente para o backend Python (SSE Streaming, Execução do AIAgent, POSTs)
        .fallback(proxy::proxy_handler)
        .layer(cors)
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
            return (
                [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
                Html(content),
            )
                .into_response();
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
        session_detail::NativeOutcome::Ready(mut val) => {
            // Aplicar sanitização rápida de segredos em Rust
            redact::redact_value(&mut val);

            let json_bytes =
                serde_json::to_vec(&serde_json::json!({ "session": val })).unwrap_or_default();

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
        session_detail::NativeOutcome::Proxy => proxy::proxy_handler(State(state), req).await,
        session_detail::NativeOutcome::ProxyAndCache => {
            // Cache-through: Python faz o merge canônico uma vez; a cauda da
            // resposta alimenta o tail_cache e os polls seguintes saem do Rust.
            let resp = proxy::proxy_handler(State(state.clone()), req).await;
            cache_response_tail(&state, &query, resp).await
        }
    }
}

/// Extrai a cauda da resposta do Python e a guarda no tail_cache (best-effort;
/// nunca altera a resposta em si).
async fn cache_response_tail(
    state: &AppState,
    query: &SessionQuery,
    resp: Response,
) -> Response {
    let limit = match query.msg_limit {
        Some(l) if l > 0 => l,
        _ => return resp,
    };
    if query.msg_before.is_some() {
        return resp;
    }
    let file = match session_detail::sidecar_path(&state.haos_home, &query.session_id) {
        Some(f) => f,
        None => return resp,
    };
    // Só cacheia respostas 200 JSON sem encoding (o proxy repassa gzip ao
    // cliente; aqui pedimos ao Python a versão raw quando possível).
    let status = resp.status();
    if !status.is_success() {
        return resp;
    }
    let (parts, body) = resp.into_parts();
    let bytes = match axum::body::to_bytes(body, 32 * 1024 * 1024).await {
        Ok(b) => b,
        Err(_) => {
            return Response::from_parts(parts, axum::body::Body::empty());
        }
    };
    if let Some(val) = parse_tail_from_response(&bytes, limit) {
        session_detail::tail_cache::store(&file, val.0, val.1);
    }
    Response::from_parts(parts, axum::body::Body::from(bytes))
}

fn parse_tail_from_response(
    bytes: &[u8],
    _limit: usize,
) -> Option<(usize, Vec<serde_json::Value>)> {
    // gzip do Python? descompacta.
    let owned: std::borrow::Cow<[u8]> = if bytes.starts_with(&[0x1f, 0x8b]) {
        use flate2::read::GzDecoder;
        use std::io::Read;
        let mut d = GzDecoder::new(bytes);
        let mut buf = Vec::new();
        d.read_to_end(&mut buf).ok()?;
        std::borrow::Cow::Owned(buf)
    } else {
        std::borrow::Cow::Borrowed(bytes)
    };
    let v: serde_json::Value = serde_json::from_slice(&owned).ok()?;
    let session = v.get("session")?;
    // Não cacheia sessões ativas: o sidecar muda a cada segundo.
    let active = session
        .get("active_stream_id")
        .map(|x| !x.is_null() && x.as_str().map(|s| !s.is_empty()).unwrap_or(false))
        .unwrap_or(false);
    if active {
        return None;
    }
    let msgs = session.get("messages")?.as_array()?.clone();
    if msgs.is_empty() {
        return None;
    }
    let total = session
        .get("message_count")
        .and_then(|x| x.as_u64())
        .unwrap_or(msgs.len() as u64) as usize;
    // Armazena todas as mensagens retornadas pelo Python no cache, para que
    // o lookup posterior possa fatiar qualquer limit <= msgs.len().
    Some((total, msgs))
}

async fn raggraph_proxy_handler(
    State(state): State<AppState>,
    req: Request,
) -> Response {
    let uri = req.uri();
    let path_and_query = uri
        .path_and_query()
        .map(|pq| pq.as_str())
        .unwrap_or_else(|| uri.path());
    let target_url = format!("http://127.0.0.1:8799{path_and_query}");

    let method = req.method().clone();
    let mut client_req = state.http_client.request(method, &target_url);

    for (k, v) in req.headers() {
        if k != header::HOST {
            client_req = client_req.header(k, v);
        }
    }
    client_req = client_req.header(header::HOST, "127.0.0.1:8799");

    let body_bytes = match axum::body::to_bytes(req.into_body(), 10 * 1024 * 1024).await {
        Ok(b) => b,
        Err(e) => return (StatusCode::BAD_REQUEST, format!("Body error: {e}")).into_response(),
    };
    if !body_bytes.is_empty() {
        client_req = client_req.body(body_bytes);
    }

    match client_req.send().await {
        Ok(resp) => {
            let status = resp.status();
            let mut response_builder = Response::builder().status(status);
            for (k, v) in resp.headers() {
                response_builder = response_builder.header(k, v);
            }
            let bytes = resp.bytes().await.unwrap_or_default();
            response_builder
                .body(axum::body::Body::from(bytes))
                .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
        }
        Err(err) => (
            StatusCode::BAD_GATEWAY,
            format!("RAGGraph upstream error: {err}"),
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
    let forward_url = format!(
        "{}/api/session/status?session_id={session_id}",
        state.backend_url
    );
    match state.http_client.get(&forward_url).send().await {
        Ok(resp) => {
            let status_code = resp.status();
            let headers = resp.headers().clone();
            if let Ok(bytes) = resp.bytes().await {
                if status_code.is_success() && !session_id.is_empty() {
                    state
                        .status_cache
                        .insert(&session_id, bytes.to_vec(), vec![]);
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
