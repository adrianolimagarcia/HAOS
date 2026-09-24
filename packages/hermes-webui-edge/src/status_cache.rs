//! Cache de alta performance para /api/session/status no Rust Edge.
//!
//! Blinda o backend Python de polling contínuo (a cada 2-6 segundos por aba do navegador),
//! respondendo em sub-milissegundos com TTL configurável (default 2.5s) e repassando
//! ao backend apenas quando o cache expira.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const MAX_ENTRIES: usize = 256;

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
            // Remove expired entries first, then enforce a hard cardinality
            // bound so attacker-controlled/session-generated IDs cannot grow
            // the proxy heap indefinitely.
            map.retain(|_, v| v.cached_at.elapsed() < Duration::from_secs(30));
            if map.len() >= MAX_ENTRIES && !map.contains_key(session_id) {
                if let Some(oldest) = map
                    .iter()
                    .min_by_key(|(_, value)| value.cached_at)
                    .map(|(key, _)| key.clone())
                {
                    map.remove(&oldest);
                }
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

#[cfg(test)]
mod tests {
    use super::*;

    fn body(cache: &SessionStatusCache, session_id: &str) -> Vec<u8> {
        cache
            .get(session_id)
            .expect("cached status should be present")
            .0
    }

    #[test]
    fn expires_entries_after_ttl() {
        let cache = SessionStatusCache::new(Duration::ZERO);
        cache.insert("session", b"expired".to_vec(), vec![]);

        assert!(cache.get("session").is_none());
    }

    #[test]
    fn updates_an_existing_key_without_increasing_cardinality() {
        let cache = SessionStatusCache::new(Duration::from_secs(30));
        cache.insert(
            "session",
            b"before".to_vec(),
            vec![("x-version".into(), "1".into())],
        );
        cache.insert(
            "session",
            b"after".to_vec(),
            vec![("x-version".into(), "2".into())],
        );

        assert_eq!(body(&cache, "session"), b"after");
        assert_eq!(
            cache
                .get("session")
                .expect("updated status should be present")
                .1,
            vec![("x-version".into(), "2".into())]
        );
        assert_eq!(cache.entries.lock().expect("cache lock").len(), 1);
    }

    #[test]
    fn caps_cache_at_256_entries() {
        let cache = SessionStatusCache::new(Duration::from_secs(30));
        for index in 0..MAX_ENTRIES {
            cache.insert(&format!("session-{index}"), vec![index as u8], vec![]);
        }

        cache.insert("session-new", b"new".to_vec(), vec![]);

        let present = (0..MAX_ENTRIES)
            .filter(|index| cache.get(&format!("session-{index}")).is_some())
            .count();
        assert_eq!(present, MAX_ENTRIES - 1);
        assert!(cache.get("session-new").is_some());
        assert_eq!(cache.entries.lock().expect("cache lock").len(), MAX_ENTRIES);
    }

    #[test]
    fn evicts_the_oldest_entry_when_limit_is_reached() {
        let cache = SessionStatusCache::new(Duration::from_secs(30));
        cache.insert("oldest", b"oldest".to_vec(), vec![]);
        std::thread::sleep(Duration::from_millis(2));
        for index in 0..(MAX_ENTRIES - 1) {
            cache.insert(&format!("session-{index}"), vec![index as u8], vec![]);
        }

        cache.insert("session-new", b"new".to_vec(), vec![]);

        assert!(cache.get("oldest").is_none());
        assert!(cache.get("session-0").is_some());
        assert!(cache.get("session-new").is_some());
    }
}
