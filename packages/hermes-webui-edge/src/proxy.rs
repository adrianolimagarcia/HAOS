use crate::AppState;
use axum::{
    body::Body,
    extract::{Request, State},
    http::{header, StatusCode},
    response::{IntoResponse, Response},
};
use futures_util::TryStreamExt;
use std::time::Duration;

pub async fn proxy_handler(State(state): State<AppState>, req: Request) -> Response {
    let method = req.method().clone();
    let uri = req.uri();
    let path_query = uri.path_and_query().map(|pq| pq.as_str()).unwrap_or(uri.path());
    let target_url = format!("{}{path_query}", state.backend_url);

    let is_sse = path_query.contains("/stream") || path_query.contains("/chat");

    let mut client_req = state.http_client.request(method, &target_url);

    // Repassar cabeçalhos do cliente para o backend Python
    let original_host = req.headers().get(header::HOST).cloned();
    for (key, val) in req.headers() {
        if key != header::HOST && key != header::CONNECTION {
            client_req = client_req.header(key.clone(), val.clone());
        }
    }

    // Encaminhar cabeçalhos de Reverse Proxy corretos para validação de CSRF e Same-Origin:
    // O Python compara o Origin do browser (ex: http://100.77.31.78:8787) com o Host recebido
    if let Some(host_val) = original_host {
        client_req = client_req
            .header(header::HOST, host_val.clone())
            .header("X-Forwarded-Host", host_val.clone())
            .header("X-Real-Host", host_val);
    }
    client_req = client_req.header("X-Forwarded-Proto", "http");

    // Configuração de timeout: infinito para SSE, 60s para requisições normais
    if !is_sse {
        client_req = client_req.timeout(Duration::from_secs(60));
    }

    // Encaminhar corpo da requisição
    let body_bytes = match axum::body::to_bytes(req.into_body(), 10 * 1024 * 1024).await {
        Ok(b) => b,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                format!("Failed to read request body: {e}"),
            )
                .into_response();
        }
    };

    if !body_bytes.is_empty() {
        client_req = client_req.body(body_bytes);
    }

    let upstream_res = match client_req.send().await {
        Ok(res) => res,
        Err(e) => {
            return (
                StatusCode::BAD_GATEWAY,
                format!("Hermes WebUI backend unreachable: {e}"),
            )
                .into_response();
        }
    };

    let status = upstream_res.status();
    let upstream_headers = upstream_res.headers().clone();

    let stream = upstream_res
        .bytes_stream()
        .map_ok(axum::body::Bytes::from)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e));

    let body = Body::from_stream(stream);

    let mut response = Response::builder().status(status);

    for (k, v) in upstream_headers.iter() {
        // Ignora hop-by-hop headers
        if k != header::TRANSFER_ENCODING && k != header::CONNECTION {
            response = response.header(k, v);
        }
    }

    // Se for SSE, assegurar cabeçalhos ideais anti-buffering
    if is_sse {
        response = response
            .header(header::CONTENT_TYPE, "text/event-stream")
            .header(header::CACHE_CONTROL, "no-cache, no-transform")
            .header("X-Accel-Buffering", "no");
    }

    match response.body(body) {
        Ok(res) => res,
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
    }
}
