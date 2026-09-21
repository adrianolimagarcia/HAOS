//! Cache de alta performance para /api/session/status no Rust Edge.
//!
//! Blinda o backend Python de polling contínuo (a cada 2-6 segundos por aba do navegador),
//! respondendo em sub-milissegundos com TTL configurável (default 2.5s) e repassando
//! ao backend apenas quando o cache expira.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Clone)]
struct CachedStatus {
    response_body: Vec<u8>,
    headers: Vec<(String, String)>,
    cached_at: Instant,
}

#[derive(Clone)]
pub struct SessionStatusCache {
    entries: Arc<Mutex<HashMap<String, CachedStatus>>>,
    ttl: Duration,
}

impl Default for SessionStatusCache {
    fn default() -> Self {
        Self::new(Duration::from_millis(2500))
    }
}

impl SessionStatusCache {
    pub fn new(ttl: Duration) -> Self {
        Self {
            entries: Arc::new(Mutex::new(HashMap::new())),
            ttl,
        }
    }

    pub fn get(&self, session_id: &str) -> Option<(Vec<u8>, Vec<(String, String)>)> {
        let mut map = self.entries.lock().ok()?;
        if let Some(entry) = map.get(session_id) {
            if entry.cached_at.elapsed() < self.ttl {
                return Some((entry.response_body.clone(), entry.headers.clone()));
            } else {
                map.remove(session_id);
            }
        }
        None
    }

    pub fn insert(&self, session_id: &str, body: Vec<u8>, headers: Vec<(String, String)>) {
        if let Ok(mut map) = self.entries.lock() {
            // Evita crescimento descontrolado
            if map.len() > 1000 {
                map.retain(|_, v| v.cached_at.elapsed() < Duration::from_secs(30));
            }
            map.insert(
                session_id.to_string(),
                CachedStatus {
                    response_body: body,
                    headers,
                    cached_at: Instant::now(),
                },
            );
        }
    }

    pub fn invalidate(&self, session_id: &str) {
        if let Ok(mut map) = self.entries.lock() {
            map.remove(session_id);
        }
    }
}
