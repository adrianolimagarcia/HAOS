//! HAOS DNS — resolvedor local com cache e upstreams em UDP, DoT (RFC 7858) e DoH (RFC 8484).
//!
//! Escopo: este daemon serve os processos do PRÓPRIO nó em 127.0.0.1:53 e escolhe como falar
//! com os resolvedores públicos. Servir DoT/DoH PARA clientes exigiria certificado no appliance
//! e não muda nada aqui (o cliente é local), então o que se configura é o transporte de SAÍDA.
//!
//! Invariante: todo upstream carrega o IP explicitamente. DoT valida o certificado pelo
//! `#server_name` (SNI) e DoH pelo `#ip` fixado na URL — assim o daemon NUNCA precisa resolver
//! o nome do próprio upstream, o que seria circular (ele é o resolvedor do nó).
//!
//! Configuração: `/etc/haos/dns.toml` (ou `--config <path>`); sem arquivo, valem os quatro
//! resolvedores públicos em UDP puro de sempre.
//!
//! ```toml
//! listen = "127.0.0.1:53"
//! cache_ttl_seconds = 60
//! cache_entries = 10000
//! upstreams = [
//!   "9.9.9.9@853#dns.quad9.net",              # DoT
//!   "https://cloudflare-dns.com/dns-query#1.1.1.1",  # DoH
//!   "9.9.9.9:53",                             # UDP puro (fallback)
//! ]
//! ```

use std::net::{IpAddr, SocketAddr};
use std::num::NonZeroUsize;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};

use lru::LruCache;
use serde::Deserialize;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpStream, UdpSocket};
use tokio::sync::Mutex;
use tokio_rustls::rustls::pki_types::ServerName;
use tokio_rustls::rustls::{ClientConfig, RootCertStore};
use tokio_rustls::TlsConnector;

const DEFAULT_CONFIG_PATH: &str = "/etc/haos/dns.toml";
const DEFAULT_LISTEN: &str = "127.0.0.1:53";
const DEFAULT_CACHE_TTL_SECS: u64 = 60;
const DEFAULT_CACHE_ENTRIES: usize = 10_000;
const UPSTREAM_TIMEOUT: Duration = Duration::from_millis(1200);
const DEFAULT_UPSTREAMS: [&str; 4] = ["9.9.9.9:53", "1.1.1.1:53", "8.8.8.8:53", "208.67.222.222:53"];

#[derive(Clone)]
struct CachedRecord {
    data: Vec<u8>,
    expires_at: Instant,
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum Upstream {
    /// UDP puro (`9.9.9.9:53`).
    Udp(SocketAddr),
    /// DoT (`9.9.9.9@853#dns.quad9.net`): TCP + TLS com framing de 2 bytes.
    Dot { addr: SocketAddr, server_name: String },
    /// DoH (`https://cloudflare-dns.com/dns-query#1.1.1.1`): POST application/dns-message.
    Doh { addr: SocketAddr, host: String, path: String },
}

impl Upstream {
    fn label(&self) -> String {
        match self {
            Upstream::Udp(addr) => format!("udp://{addr}"),
            Upstream::Dot { addr, server_name } => format!("tls://{addr}#{server_name}"),
            Upstream::Doh { addr, host, path } => format!("https://{host}{path}#{addr}"),
        }
    }
}

fn parse_addr_with_default_port(raw: &str, default_port: u16) -> Result<SocketAddr, String> {
    let raw = raw.trim();
    if raw.is_empty() {
        return Err("endereço de upstream vazio".to_string());
    }
    let (host_raw, port) = match raw.rsplit_once('@').or_else(|| raw.rsplit_once(':')) {
        Some((host, port)) => (
            host,
            port.parse::<u16>()
                .map_err(|_| format!("porta inválida em '{raw}'"))?,
        ),
        None => (raw, default_port),
    };
    // Literais IPv6 chegam como [::1]; aceita as duas formas.
    let host = host_raw
        .trim()
        .trim_start_matches('[')
        .trim_end_matches(']');
    let ip: IpAddr = host
        .parse()
        .map_err(|_| format!("upstream '{raw}' precisa de um IP literal (sem bootstrap de nome)"))?;
    Ok(SocketAddr::new(ip, port))
}

fn parse_upstream(spec: &str) -> Result<Upstream, String> {
    let spec = spec.trim();
    if spec.is_empty() {
        return Err("upstream vazio".to_string());
    }

    if let Some(rest) = spec.strip_prefix("https://") {
        let (url, pin) = match rest.split_once('#') {
            Some((url, pin)) => (url, Some(pin.trim())),
            None => (rest, None),
        };
        let (host, path) = match url.split_once('/') {
            Some((host, path)) => (host.trim(), format!("/{}", path.trim())),
            None => (url.trim(), "/dns-query".to_string()),
        };
        if host.is_empty() {
            return Err(format!("DoH sem host: '{spec}'"));
        }
        let pin = pin.filter(|p| !p.is_empty()).ok_or_else(|| {
            format!("DoH sem IP fixado: use https://{host}/dns-query#<ip> (o daemon não resolve o próprio upstream)")
        })?;
        let addr = if pin.contains(':') {
            pin.parse::<SocketAddr>()
                .map_err(|_| format!("IP do DoH inválido em '{spec}'"))?
        } else {
            format!("{pin}:443")
                .parse::<SocketAddr>()
                .map_err(|_| format!("IP do DoH inválido em '{spec}'"))?
        };
        return Ok(Upstream::Doh {
            addr,
            host: host.to_string(),
            path,
        });
    }

    if let Some((head, server_name)) = spec.split_once('#') {
        let server_name = server_name.trim();
        if server_name.is_empty() {
            return Err(format!("DoT sem server_name: '{spec}'"));
        }
        return Ok(Upstream::Dot {
            addr: parse_addr_with_default_port(head, 853)?,
            server_name: server_name.to_string(),
        });
    }

    Ok(Upstream::Udp(parse_addr_with_default_port(spec, 53)?))
}

fn parse_upstreams(specs: &[String]) -> Result<Vec<Upstream>, String> {
    let mut out = Vec::with_capacity(specs.len());
    for spec in specs {
        out.push(parse_upstream(spec)?);
    }
    if out.is_empty() {
        return Err("lista de upstreams vazia".to_string());
    }
    Ok(out)
}

/// Corpo de uma resposta DoH: valida o status, recusa compressão não pedida e desfaz o
/// `Transfer-Encoding: chunked` quando o servidor escolhe esse caminho.
fn http_dns_body(head: &str, body: &[u8]) -> Result<Vec<u8>, String> {
    let status = head.lines().next().unwrap_or("");
    if !status.contains(" 200") {
        return Err(format!("DoH respondeu '{}'", status.trim()));
    }
    let mut chunked = false;
    for line in head.lines().skip(1) {
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        let name = name.trim().to_ascii_lowercase();
        let value = value.trim().to_ascii_lowercase();
        if name == "transfer-encoding" && value.contains("chunked") {
            chunked = true;
        }
        if name == "content-encoding" && value != "identity" {
            return Err(format!("DoH com Content-Encoding '{value}' (não pedimos compressão)"));
        }
    }
    if chunked {
        return dechunk(body);
    }
    Ok(body.to_vec())
}

fn dechunk(body: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    let mut rest = body;
    loop {
        let crlf = rest
            .windows(2)
            .position(|w| w == b"\r\n")
            .ok_or_else(|| "chunk sem CRLF".to_string())?;
        let size_line = std::str::from_utf8(&rest[..crlf])
            .map_err(|_| "tamanho de chunk não-UTF8".to_string())?;
        let size_hex = size_line.split(';').next().unwrap_or("").trim();
        let size = usize::from_str_radix(size_hex, 16)
            .map_err(|_| format!("tamanho de chunk inválido: '{size_hex}'"))?;
        if size == 0 {
            return Ok(out);
        }
        let start = crlf + 2;
        let end = start
            .checked_add(size)
            .filter(|end| *end <= rest.len())
            .ok_or_else(|| "chunk maior que o corpo recebido".to_string())?;
        out.extend_from_slice(&rest[start..end]);
        rest = rest[end..].strip_prefix(b"\r\n".as_slice()).unwrap_or(&rest[end..]);
    }
}

#[derive(Deserialize, Default)]
struct FileConfig {
    /// Nomes canônicos são os que o distro já publicava (`listen_addr`,
    /// `cache_max_entries`) — o binário antigo ignorava o arquivo inteiro, então
    /// renomear as chaves silenciosamente descartaria a config de quem já a tinha.
    #[serde(alias = "listen")]
    listen_addr: Option<String>,
    cache_ttl_seconds: Option<u64>,
    #[serde(alias = "cache_entries")]
    cache_max_entries: Option<usize>,
    upstreams: Option<Vec<String>>,
}

fn load_file_config(path: &Path) -> Result<FileConfig, String> {
    if !path.exists() {
        return Ok(FileConfig::default());
    }
    let text = std::fs::read_to_string(path)
        .map_err(|e| format!("não consegui ler {}: {e}", path.display()))?;
    toml::from_str(&text).map_err(|e| format!("{} inválido: {e}", path.display()))
}

struct DnsServer {
    listen_addr: SocketAddr,
    upstreams: Vec<Upstream>,
    cache: Arc<Mutex<LruCache<Vec<u8>, CachedRecord>>>,
    cache_ttl: Duration,
    tls: TlsConnector,
}

impl DnsServer {
    fn new(
        listen_addr: SocketAddr,
        upstreams: Vec<Upstream>,
        cache_entries: usize,
        cache_ttl: Duration,
    ) -> Result<Self, String> {
        let mut roots = RootCertStore::empty();
        roots.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
        let tls_config = ClientConfig::builder()
            .with_root_certificates(roots)
            .with_no_client_auth();
        let capacity = NonZeroUsize::new(cache_entries.max(1))
            .ok_or_else(|| "cache_entries precisa ser >= 1".to_string())?;
        Ok(Self {
            listen_addr,
            upstreams,
            cache: Arc::new(Mutex::new(LruCache::new(capacity))),
            cache_ttl,
            tls: TlsConnector::from(Arc::new(tls_config)),
        })
    }

    async fn run(&self) -> Result<(), Box<dyn std::error::Error>> {
        let socket = Arc::new(UdpSocket::bind(self.listen_addr).await?);
        let labels: Vec<String> = self.upstreams.iter().map(Upstream::label).collect();
        println!(
            "[haos-dns] Listening on udp://{} (cache: {} entradas, {}s | upstreams: {})",
            self.listen_addr,
            self.cache.lock().await.cap().get(),
            self.cache_ttl.as_secs(),
            labels.join(", ")
        );

        let mut buf = [0u8; 4096];
        loop {
            let (len, src) = match socket.recv_from(&mut buf).await {
                Ok(res) => res,
                Err(e) => {
                    eprintln!("[haos-dns] recv_from error: {}", e);
                    continue;
                }
            };

            let query_data = buf[..len].to_vec();
            let socket_clone = Arc::clone(&socket);
            let upstreams = self.upstreams.clone();
            let cache = Arc::clone(&self.cache);
            let cache_ttl = self.cache_ttl;
            let tls = self.tls.clone();

            tokio::spawn(async move {
                if query_data.len() < 12 {
                    return;
                }

                let query_id = (query_data[0], query_data[1]);
                let question_key = query_data[12..].to_vec();

                // 1. Cache (LRU: entradas expiradas saem e o mapa tem teto — o HashMap anterior
                //    crescia sem limite, porque nada removia chave vencida).
                {
                    let mut c = cache.lock().await;
                    // O clone fecha o empréstimo de `c` antes do `pop` da entrada vencida.
                    let state = c
                        .get(&question_key)
                        .map(|entry| (entry.data.clone(), Instant::now() < entry.expires_at));
                    let hit = match state {
                        Some((data, true)) => Some(data),
                        Some(_) => {
                            c.pop(&question_key);
                            None
                        }
                        None => None,
                    };
                    if let Some(mut resp) = hit {
                        if resp.len() >= 2 {
                            resp[0] = query_id.0;
                            resp[1] = query_id.1;
                            let _ = socket_clone.send_to(&resp, src).await;
                            return;
                        }
                    }
                }

                // 2. Encaminha para os upstreams (a primeira resposta válida vence).
                for upstream in upstreams {
                    let answer = tokio::time::timeout(
                        UPSTREAM_TIMEOUT,
                        forward(&tls, &upstream, &query_data),
                    )
                    .await;
                    let Ok(Ok(response_data)) = answer else {
                        continue;
                    };
                    if response_data.len() < 12 {
                        continue;
                    }
                    // Resposta de outro query id não é resposta desta pergunta.
                    if response_data[0] != query_data[0] || response_data[1] != query_data[1] {
                        continue;
                    }
                    let _ = socket_clone.send_to(&response_data, src).await;

                    let mut cached = response_data.clone();
                    cached[0] = 0;
                    cached[1] = 0;
                    cache.lock().await.put(
                        question_key,
                        CachedRecord {
                            data: cached,
                            expires_at: Instant::now() + cache_ttl,
                        },
                    );
                    return;
                }
            });
        }
    }
}

async fn forward(tls: &TlsConnector, upstream: &Upstream, query: &[u8]) -> Result<Vec<u8>, String> {
    match upstream {
        Upstream::Udp(addr) => forward_udp(*addr, query).await,
        Upstream::Dot { addr, server_name } => forward_dot(tls, *addr, server_name, query).await,
        Upstream::Doh { addr, host, path } => forward_doh(tls, *addr, host, path, query).await,
    }
}

async fn forward_udp(addr: SocketAddr, query: &[u8]) -> Result<Vec<u8>, String> {
    let client = UdpSocket::bind("0.0.0.0:0")
        .await
        .map_err(|e| format!("bind efêmero falhou: {e}"))?;
    client
        .send_to(query, addr)
        .await
        .map_err(|e| format!("envio UDP para {addr} falhou: {e}"))?;
    let mut resp_buf = [0u8; 4096];
    let (resp_len, _) = client
        .recv_from(&mut resp_buf)
        .await
        .map_err(|e| format!("resposta UDP de {addr} falhou: {e}"))?;
    Ok(resp_buf[..resp_len].to_vec())
}

async fn connect_tls(
    tls: &TlsConnector,
    addr: SocketAddr,
    server_name: &str,
) -> Result<tokio_rustls::client::TlsStream<TcpStream>, String> {
    let stream = TcpStream::connect(addr)
        .await
        .map_err(|e| format!("TCP para {addr} falhou: {e}"))?;
    let name = ServerName::try_from(server_name.to_string())
        .map_err(|_| format!("server_name inválido: '{server_name}'"))?;
    tls.connect(name, stream)
        .await
        .map_err(|e| format!("TLS com {server_name} ({addr}) falhou: {e}"))
}

/// DoT: a mensagem DNS vai precedida do tamanho em 2 bytes (RFC 7858 §3.3).
async fn forward_dot(
    tls: &TlsConnector,
    addr: SocketAddr,
    server_name: &str,
    query: &[u8],
) -> Result<Vec<u8>, String> {
    let mut stream = connect_tls(tls, addr, server_name).await?;
    let mut framed = Vec::with_capacity(query.len() + 2);
    framed.extend_from_slice(&(query.len() as u16).to_be_bytes());
    framed.extend_from_slice(query);
    stream
        .write_all(&framed)
        .await
        .map_err(|e| format!("escrita DoT falhou: {e}"))?;
    stream.flush().await.map_err(|e| format!("flush DoT falhou: {e}"))?;

    let mut len_buf = [0u8; 2];
    stream
        .read_exact(&mut len_buf)
        .await
        .map_err(|e| format!("leitura DoT falhou: {e}"))?;
    let resp_len = u16::from_be_bytes(len_buf) as usize;
    let mut resp = vec![0u8; resp_len];
    stream
        .read_exact(&mut resp)
        .await
        .map_err(|e| format!("corpo DoT falhou: {e}"))?;
    Ok(resp)
}

/// DoH: POST com Content-Type/Accept application/dns-message (RFC 8484 §4.1). HTTP/1.1 sem
/// ALPN — negociar h2 exigiria outro cliente e não muda o resultado.
async fn forward_doh(
    tls: &TlsConnector,
    addr: SocketAddr,
    host: &str,
    path: &str,
    query: &[u8],
) -> Result<Vec<u8>, String> {
    let mut stream = connect_tls(tls, addr, host).await?;
    let request = format!(
        "POST {path} HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/dns-message\r\nAccept: application/dns-message\r\nContent-Length: {len}\r\nConnection: close\r\n\r\n",
        path = path,
        host = host,
        len = query.len()
    );
    stream
        .write_all(request.as_bytes())
        .await
        .map_err(|e| format!("cabeçalho DoH falhou: {e}"))?;
    stream
        .write_all(query)
        .await
        .map_err(|e| format!("corpo DoH falhou: {e}"))?;
    stream.flush().await.map_err(|e| format!("flush DoH falhou: {e}"))?;

    let mut raw = Vec::with_capacity(2048);
    let mut chunk = [0u8; 2048];
    let head_end = loop {
        if let Some(pos) = raw.windows(4).position(|w| w == b"\r\n\r\n") {
            break pos;
        }
        let read = stream
            .read(&mut chunk)
            .await
            .map_err(|e| format!("leitura DoH falhou: {e}"))?;
        if read == 0 {
            return Err("DoH fechou a conexão antes dos cabeçalhos".to_string());
        }
        raw.extend_from_slice(&chunk[..read]);
        if raw.len() > 64 * 1024 {
            return Err("cabeçalhos DoH grandes demais".to_string());
        }
    };

    let head = std::str::from_utf8(&raw[..head_end])
        .map_err(|_| "cabeçalhos DoH não-UTF8".to_string())?
        .to_string();
    let mut body = raw[head_end + 4..].to_vec();

    // Com Content-Length o corpo é exato; sem ele (chunked/close) lê até o fim.
    let content_length = head.lines().skip(1).find_map(|line| {
        let (name, value) = line.split_once(':')?;
        if name.trim().eq_ignore_ascii_case("content-length") {
            value.trim().parse::<usize>().ok()
        } else {
            None
        }
    });
    match content_length {
        Some(want) => {
            while body.len() < want {
                let read = stream
                    .read(&mut chunk)
                    .await
                    .map_err(|e| format!("corpo DoH falhou: {e}"))?;
                if read == 0 {
                    return Err(format!(
                        "DoH entregou {} de {} bytes",
                        body.len(),
                        want
                    ));
                }
                body.extend_from_slice(&chunk[..read]);
            }
            body.truncate(want);
        }
        None => loop {
            let read = stream
                .read(&mut chunk)
                .await
                .map_err(|e| format!("corpo DoH falhou: {e}"))?;
            if read == 0 {
                break;
            }
            body.extend_from_slice(&chunk[..read]);
            if body.len() > 64 * 1024 {
                return Err("corpo DoH grande demais".to_string());
            }
        },
    }

    http_dns_body(&head, &body)
}

fn config_path_from_args() -> PathBuf {
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--config" => {
                if let Some(path) = args.next() {
                    return PathBuf::from(path);
                }
            }
            "--help" | "-h" => {
                println!("uso: haos-dns [--config /etc/haos/dns.toml]");
                std::process::exit(0);
            }
            other if !other.starts_with('-') => return PathBuf::from(other),
            _ => {}
        }
    }
    PathBuf::from(DEFAULT_CONFIG_PATH)
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config_path = config_path_from_args();
    let file_config = load_file_config(&config_path)?;

    let listen_addr: SocketAddr = file_config
        .listen_addr
        .as_deref()
        .unwrap_or(DEFAULT_LISTEN)
        .parse()
        .map_err(|e| format!("listen inválido: {e}"))?;
    let specs = file_config
        .upstreams
        .unwrap_or_else(|| DEFAULT_UPSTREAMS.iter().map(|s| s.to_string()).collect());
    let upstreams = parse_upstreams(&specs)?;
    let cache_ttl = Duration::from_secs(file_config.cache_ttl_seconds.unwrap_or(DEFAULT_CACHE_TTL_SECS));
    let cache_entries = file_config.cache_max_entries.unwrap_or(DEFAULT_CACHE_ENTRIES);

    println!("========================================================");
    println!("  HAOS DNS - Resolvedor com cache (UDP/DoT/DoH)         ");
    println!("========================================================");
    if config_path.exists() {
        println!("[haos-dns] Config: {}", config_path.display());
    }

    let server = DnsServer::new(listen_addr, upstreams, cache_entries, cache_ttl)?;
    server.run().await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_plain_udp_upstream() {
        assert_eq!(
            parse_upstream("9.9.9.9:53").unwrap(),
            Upstream::Udp("9.9.9.9:53".parse().unwrap())
        );
        // Porta default 53 quando ausente.
        assert_eq!(
            parse_upstream("1.1.1.1").unwrap(),
            Upstream::Udp("1.1.1.1:53".parse().unwrap())
        );
    }

    #[test]
    fn parses_dot_upstream_with_default_port() {
        assert_eq!(
            parse_upstream("9.9.9.9@853#dns.quad9.net").unwrap(),
            Upstream::Dot {
                addr: "9.9.9.9:853".parse().unwrap(),
                server_name: "dns.quad9.net".to_string()
            }
        );
        // Sem porta explícita o DoT assume 853.
        assert_eq!(
            parse_upstream("1.1.1.1#cloudflare-dns.com").unwrap(),
            Upstream::Dot {
                addr: "1.1.1.1:853".parse().unwrap(),
                server_name: "cloudflare-dns.com".to_string()
            }
        );
    }

    #[test]
    fn parses_doh_upstream_and_requires_pinned_ip() {
        assert_eq!(
            parse_upstream("https://cloudflare-dns.com/dns-query#1.1.1.1").unwrap(),
            Upstream::Doh {
                addr: "1.1.1.1:443".parse().unwrap(),
                host: "cloudflare-dns.com".to_string(),
                path: "/dns-query".to_string()
            }
        );
        // Sem IP fixado o daemon teria que resolver o próprio upstream (circular).
        let err = parse_upstream("https://cloudflare-dns.com/dns-query").unwrap_err();
        assert!(err.contains("IP fixado"), "erro inesperado: {err}");
    }

    #[test]
    fn rejects_hostnames_and_garbage() {
        assert!(parse_upstream("dns.quad9.net:53").is_err());
        assert!(parse_upstream("9.9.9.9@porta#dns.quad9.net").is_err());
        assert!(parse_upstream("9.9.9.9#").is_err());
        assert!(parse_upstream("").is_err());
        assert!(parse_upstreams(&[]).is_err());
    }

    #[test]
    fn parses_content_length_body() {
        let head = "HTTP/1.1 200 OK\r\nContent-Type: application/dns-message\r\nContent-Length: 4";
        assert_eq!(http_dns_body(head, b"\x00\x01\x02\x03").unwrap(), b"\x00\x01\x02\x03");
    }

    #[test]
    fn parses_chunked_body_and_rejects_compression_and_errors() {
        let head = "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked";
        assert_eq!(
            http_dns_body(head, b"4\r\nabcd\r\n0\r\n\r\n").unwrap(),
            b"abcd"
        );
        let compressed = "HTTP/1.1 200 OK\r\nContent-Encoding: gzip";
        assert!(http_dns_body(compressed, b"x").is_err());
        let not_found = "HTTP/1.1 404 Not Found";
        assert!(http_dns_body(not_found, b"x").is_err());
    }

    #[test]
    fn dechunk_rejects_truncated_chunk() {
        assert!(dechunk(b"10\r\nabc").is_err());
    }

    /// O arquivo que o distro entrega é lido por ESTE binário: um rename de chave ou um
    /// upstream sem IP fixado só apareceria no boot do appliance, com o DNS do nó fora.
    #[test]
    fn shipped_distro_config_parses() {
        let text =
            include_str!("../../../distro/haos-linux/config/includes.chroot/etc/haos/dns.toml");
        let cfg: FileConfig = toml::from_str(text).expect("config do distro não parseia");
        assert_eq!(cfg.listen_addr.as_deref(), Some("127.0.0.1:53"));
        assert!(
            cfg.cache_ttl_seconds.unwrap_or(0) > 0,
            "cache_ttl_seconds precisa ser positivo"
        );
        let upstreams = parse_upstreams(&cfg.upstreams.expect("upstreams ausentes no distro"))
            .expect("upstreams do distro inválidos");
        assert!(
            upstreams.iter().any(|u| matches!(u, Upstream::Dot { .. })),
            "o config do distro deve trazer pelo menos um upstream DoT"
        );
        assert!(
            upstreams.iter().any(|u| matches!(u, Upstream::Doh { .. })),
            "o config do distro deve trazer pelo menos um upstream DoH"
        );
        assert!(
            upstreams.iter().any(|u| matches!(u, Upstream::Udp(_))),
            "o config do distro deve manter um upstream UDP como último recurso"
        );
    }

    /// Compatibilidade: o arquivo do distro já publicava `listen_addr`/`cache_max_entries`
    /// (o binário antigo ignorava o arquivo) e os nomes curtos valem como alias.
    #[test]
    fn accepts_short_key_aliases() {
        let cfg: FileConfig =
            toml::from_str("listen = \"127.0.0.1:5353\"\ncache_entries = 7\n").unwrap();
        assert_eq!(cfg.listen_addr.as_deref(), Some("127.0.0.1:5353"));
        assert_eq!(cfg.cache_max_entries, Some(7));
    }
}
