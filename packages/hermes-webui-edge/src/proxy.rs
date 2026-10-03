use crate::AppState;
use axum::extract::ws::{Message as AxumMessage, WebSocket};
use axum::{
    body::Body,
    extract::{ws::WebSocketUpgrade, Request, State},
    http::{header, StatusCode},
    response::{IntoResponse, Response},
};
use futures_util::{SinkExt, StreamExt, TryStreamExt};
use std::time::Duration;
use tokio_tungstenite::{
    connect_async,
    tungstenite::{client::IntoClientRequest, Message as TungsteniteMessage},
};

pub async fn proxy_handler(State(state): State<AppState>, req: Request) -> Response {
    let method = req.method().clone();
    let uri = req.uri();
    let path_query = uri
        .path_and_query()
        .map(|pq| pq.as_str())
        .unwrap_or(uri.path());
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

/// Forward the dashboard event WebSocket to the Python backend. The edge
/// server must handle the upgrade explicitly; HTTP fallback cannot do so.
pub async fn events_ws_handler(
    State(state): State<AppState>,
    ws: WebSocketUpgrade,
    req: Request,
) -> impl IntoResponse {
    let path_query = req
        .uri()
        .path_and_query()
        .map(|value| value.as_str().to_owned())
        .unwrap_or_else(|| "/api/events".to_owned());
    let backend_url = state.backend_url.clone();
    let cookie = req.headers().get(header::COOKIE).cloned();

    ws.on_upgrade(move |socket| async move {
        let target = websocket_target_url(&backend_url, &path_query);
        let mut request = match target.into_client_request() {
            Ok(request) => request,
            Err(error) => {
                tracing::warn!(%error, "invalid backend events websocket URL");
                return;
            }
        };
        if let Some(cookie) = cookie {
            request.headers_mut().insert(header::COOKIE, cookie);
        }
        match connect_async(request).await {
            Ok((upstream, _)) => bridge_events_socket(socket, upstream).await,
            Err(error) => tracing::warn!(%error, "backend events websocket unavailable"),
        }
    })
}

fn websocket_target_url(backend_url: &str, path_query: &str) -> String {
    let scheme = if backend_url.starts_with("https://") {
        "wss://"
    } else {
        "ws://"
    };
    let authority = backend_url
        .trim_start_matches("http://")
        .trim_start_matches("https://")
        .trim_end_matches('/');
    format!("{scheme}{authority}{path_query}")
}

async fn bridge_events_socket(
    client: WebSocket,
    upstream: tokio_tungstenite::WebSocketStream<
        tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
    >,
) {
    let (mut client_sink, mut client_stream) = client.split();
    let (mut upstream_sink, mut upstream_stream) = upstream.split();
    loop {
        tokio::select! {
            client_message = client_stream.next() => match client_message {
                Some(Ok(message)) => {
                    if let Some(message) = axum_to_tungstenite(message) {
                        if upstream_sink.send(message).await.is_err() { break; }
                    }
                }
                _ => break,
            },
            upstream_message = upstream_stream.next() => match upstream_message {
                Some(Ok(message)) => {
                    if let Some(message) = tungstenite_to_axum(message) {
                        if client_sink.send(message).await.is_err() { break; }
                    }
                }
                _ => break,
            },
        }
    }
}

fn axum_to_tungstenite(message: AxumMessage) -> Option<TungsteniteMessage> {
    match message {
        AxumMessage::Text(value) => Some(TungsteniteMessage::Text(value.to_string().into())),
        AxumMessage::Binary(value) => Some(TungsteniteMessage::Binary(value.to_vec().into())),
        AxumMessage::Ping(value) => Some(TungsteniteMessage::Ping(value.to_vec().into())),
        AxumMessage::Pong(value) => Some(TungsteniteMessage::Pong(value.to_vec().into())),
        AxumMessage::Close(_) => Some(TungsteniteMessage::Close(None)),
    }
}

fn tungstenite_to_axum(message: TungsteniteMessage) -> Option<AxumMessage> {
    match message {
        TungsteniteMessage::Text(value) => Some(AxumMessage::Text(value.to_string().into())),
        TungsteniteMessage::Binary(value) => Some(AxumMessage::Binary(value.to_vec().into())),
        TungsteniteMessage::Ping(value) => Some(AxumMessage::Ping(value.to_vec().into())),
        TungsteniteMessage::Pong(value) => Some(AxumMessage::Pong(value.to_vec().into())),
        TungsteniteMessage::Close(_) => Some(AxumMessage::Close(None)),
        TungsteniteMessage::Frame(_) => None,
    }
}

#[cfg(test)]
mod tests {
    use super::websocket_target_url;

    #[test]
    fn websocket_proxy_preserves_event_ticket_query() {
        assert_eq!(
            websocket_target_url(
                "http://127.0.0.1:8786",
                "/api/events?ticket=abc&channel=chat-1"
            ),
            "ws://127.0.0.1:8786/api/events?ticket=abc&channel=chat-1"
        );
    }

    #[test]
    fn websocket_proxy_uses_secure_scheme_for_https_backend() {
        assert_eq!(
            websocket_target_url("https://backend.example/", "/api/events"),
            "wss://backend.example/api/events"
        );
    }
}
