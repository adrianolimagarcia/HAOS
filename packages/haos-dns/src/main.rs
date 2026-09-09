use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::net::UdpSocket;
use tokio::sync::RwLock;

#[derive(Clone)]
struct CachedRecord {
    data: Vec<u8>,
    expires_at: Instant,
}

struct DnsServer {
    listen_addr: SocketAddr,
    upstreams: Vec<SocketAddr>,
    cache: Arc<RwLock<HashMap<Vec<u8>, CachedRecord>>>,
}

impl DnsServer {
    fn new(listen_addr: SocketAddr, upstreams: Vec<SocketAddr>) -> Self {
        Self {
            listen_addr,
            upstreams,
            cache: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    async fn run(&self) -> Result<(), Box<dyn std::error::Error>> {
        let socket = Arc::new(UdpSocket::bind(self.listen_addr).await?);
        println!(
            "[haos-dns] Listening on udp://{} (Cache enabled, Upstreams: {:?})",
            self.listen_addr, self.upstreams
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

            tokio::spawn(async move {
                if query_data.len() < 12 {
                    return;
                }

                let query_id = (query_data[0], query_data[1]);
                let question_key = query_data[12..].to_vec();

                // 1. Check cache
                {
                    let r = cache.read().await;
                    if let Some(entry) = r.get(&question_key) {
                        if Instant::now() < entry.expires_at {
                            let mut resp = entry.data.clone();
                            if resp.len() >= 2 {
                                resp[0] = query_id.0;
                                resp[1] = query_id.1;
                                let _ = socket_clone.send_to(&resp, src).await;
                                return;
                            }
                        }
                    }
                }

                // 2. Forward to upstreams (first successful answer wins)
                for upstream in upstreams {
                    if let Ok(client) = UdpSocket::bind("0.0.0.0:0").await {
                        if client.send_to(&query_data, upstream).await.is_ok() {
                            let mut resp_buf = [0u8; 4096];
                            let timeout = tokio::time::timeout(
                                Duration::from_millis(1200),
                                client.recv_from(&mut resp_buf),
                            );

                            if let Ok(Ok((resp_len, _))) = timeout.await {
                                let mut response_data = resp_buf[..resp_len].to_vec();
                                // Send back to client
                                let _ = socket_clone.send_to(&response_data, src).await;

                                // Cache record for 60s
                                {
                                    let mut w = cache.write().await;
                                    response_data[0] = 0;
                                    response_data[1] = 0;
                                    w.insert(
                                        question_key,
                                        CachedRecord {
                                            data: response_data,
                                            expires_at: Instant::now() + Duration::from_secs(60),
                                        },
                                    );
                                }
                                return;
                            }
                        }
                    }
                }
            });
        }
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let listen_addr: SocketAddr = "127.0.0.1:53".parse()?;
    let upstreams: Vec<SocketAddr> = vec![
        "9.9.9.9:53".parse()?,
        "1.1.1.1:53".parse()?,
        "8.8.8.8:53".parse()?,
        "208.67.222.222:53".parse()?,
    ];

    println!("========================================================");
    println!("  HAOS DNS - High-Performance Rust Caching Resolver     ");
    println!("========================================================");

    let server = DnsServer::new(listen_addr, upstreams);
    server.run().await
}
