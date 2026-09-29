use sha2::{Digest, Sha256};
use std::time::{SystemTime, UNIX_EPOCH};

pub fn compute_sha256(text: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text.as_bytes());
    format!("{:x}", hasher.finalize())
}

pub fn compute_bundle_hash(soul: &str, identity: &str, values: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(b"soul:");
    hasher.update(soul.as_bytes());
    hasher.update(b"|identity:");
    hasher.update(identity.as_bytes());
    hasher.update(b"|values:");
    hasher.update(values.as_bytes());
    format!("{:x}", hasher.finalize())
}

pub fn now_timestamp() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

pub fn generate_id(prefix: &str) -> String {
    use rand::Rng;
    let mut rng = rand::thread_rng();
    let rand_val: u32 = rng.gen();
    let ts = (now_timestamp() * 1000.0) as u64;
    format!("{prefix}-{ts}-{rand_val:08x}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sha256_empty() {
        assert_eq!(
            compute_sha256(""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }

    #[test]
    fn test_bundle_hash_deterministic() {
        let h1 = compute_bundle_hash("soul1", "id1", "val1");
        let h2 = compute_bundle_hash("soul1", "id1", "val1");
        assert_eq!(h1, h2);

        let h3 = compute_bundle_hash("soul1", "id2", "val1");
        assert_ne!(h1, h3);
    }
}
