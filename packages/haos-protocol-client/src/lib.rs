use hmac::Mac;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::time::Duration;
use thiserror::Error;

pub const MAX_ENVELOPE_BYTES: usize = 1024 * 1024;
pub const MAX_METHOD_BYTES: usize = 256;
pub const MAX_IDEMPOTENCY_BYTES: usize = 256;
pub const MAX_DEADLINE_MS: u64 = 24 * 60 * 60 * 1000;
pub const MAX_CORRELATION_BYTES: usize = 256;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ProtocolError {
    #[error("envelope exceeds size limit")]
    TooLarge,
    #[error("invalid envelope: {0}")]
    Invalid(&'static str),
    #[error("deadline must be positive")]
    InvalidDeadline,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Envelope {
    pub version: u16,
    pub id: String,
    pub method: String,
    #[serde(default)]
    pub idempotency_key: Option<String>,
    #[serde(default)]
    pub deadline_ms: Option<u64>,
    #[serde(default)]
    pub payload: Value,
    /// Relative timeout from send time, in milliseconds; not an epoch timestamp.
    #[serde(default)]
    pub correlation_id: Option<String>,
    #[serde(default)]
    pub sequence: Option<u64>,
}
impl Envelope {
    pub fn validate(&self) -> Result<(), ProtocolError> {
        if self.version == 0 {
            return Err(ProtocolError::Invalid("version"));
        }
        if self.id.is_empty() || self.id.len() > MAX_IDEMPOTENCY_BYTES {
            return Err(ProtocolError::Invalid("id"));
        }
        if self.method.is_empty()
            || self.method.len() > MAX_METHOD_BYTES
            || !self
                .method
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'_' || b == b'-')
        {
            return Err(ProtocolError::Invalid("method"));
        }
        if let Some(k) = &self.idempotency_key {
            if k.is_empty() || k.len() > MAX_IDEMPOTENCY_BYTES {
                return Err(ProtocolError::Invalid("idempotency_key"));
            }
        }
        if let Some(d) = self.deadline_ms {
            if d == 0 {
                return Err(ProtocolError::InvalidDeadline);
            }
            if d > MAX_DEADLINE_MS {
                return Err(ProtocolError::Invalid("deadline"));
            }
        }
        if let Some(c) = &self.correlation_id {
            if c.is_empty() || c.len() > MAX_CORRELATION_BYTES {
                return Err(ProtocolError::Invalid("correlation_id"));
            }
        }
        if self.sequence == Some(0) {
            return Err(ProtocolError::Invalid("sequence"));
        }
        Ok(())
    }
    pub fn encode(&self) -> Result<Vec<u8>, ProtocolError> {
        self.validate()?;
        let bytes =
            serde_json::to_vec(self).map_err(|_| ProtocolError::Invalid("serialization"))?;
        if bytes.len() > MAX_ENVELOPE_BYTES {
            return Err(ProtocolError::TooLarge);
        }
        Ok(bytes)
    }
    pub fn timeout(&self) -> Option<Duration> {
        self.deadline_ms.map(Duration::from_millis)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RetryClass {
    Never,
    IdempotentOnly,
    /// Explicit opt-in for operations whose contract guarantees safe replay.
    AlwaysSafe,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RetryPolicy {
    pub class: RetryClass,
    pub max_attempts: u32,
}
impl RetryPolicy {
    pub fn allows(&self, idempotent: bool, attempt: u32) -> bool {
        attempt < self.max_attempts
            && match self.class {
                RetryClass::Never => false,
                RetryClass::IdempotentOnly => idempotent,
                RetryClass::AlwaysSafe => idempotent,
            }
    }
}

pub const MAX_FRAME_BYTES: usize = MAX_ENVELOPE_BYTES;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum TransportError {
    #[error("frame exceeds size limit")]
    TooLarge,
    #[error("truncated frame")]
    Truncated,
    #[error("operation cancelled")]
    Cancelled,
    #[error("circuit open")]
    CircuitOpen,
    #[error("io: {0}")]
    Io(String),
    #[error("protocol: {0}")]
    Protocol(ProtocolError),
}

pub fn write_bounded<W: std::io::Write>(
    writer: &mut W,
    envelope: &Envelope,
) -> Result<(), TransportError> {
    let body = envelope.encode().map_err(TransportError::Protocol)?;
    let len = u32::try_from(body.len()).map_err(|_| TransportError::TooLarge)?;
    writer
        .write_all(&len.to_be_bytes())
        .map_err(|e| TransportError::Io(e.to_string()))?;
    writer
        .write_all(&body)
        .map_err(|e| TransportError::Io(e.to_string()))?;
    writer
        .flush()
        .map_err(|e| TransportError::Io(e.to_string()))
}

pub fn read_bounded<R: std::io::Read>(
    reader: &mut R,
    max: usize,
) -> Result<Envelope, TransportError> {
    let mut header = [0u8; 4];
    reader.read_exact(&mut header).map_err(|e| {
        if e.kind() == std::io::ErrorKind::UnexpectedEof {
            TransportError::Truncated
        } else {
            TransportError::Io(e.to_string())
        }
    })?;
    let len = u32::from_be_bytes(header) as usize;
    if len > max || len > MAX_FRAME_BYTES {
        return Err(TransportError::TooLarge);
    }
    let mut body = vec![0u8; len];
    reader.read_exact(&mut body).map_err(|e| {
        if e.kind() == std::io::ErrorKind::UnexpectedEof {
            TransportError::Truncated
        } else {
            TransportError::Io(e.to_string())
        }
    })?;
    let envelope: Envelope = serde_json::from_slice(&body)
        .map_err(|_| TransportError::Protocol(ProtocolError::Invalid("serialization")))?;
    envelope.validate().map_err(TransportError::Protocol)?;
    Ok(envelope)
}

#[derive(Debug, Clone, Default)]
pub struct CancellationToken(std::sync::Arc<std::sync::atomic::AtomicBool>);
impl CancellationToken {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn cancel(&self) {
        self.0.store(true, std::sync::atomic::Ordering::Release);
    }
    pub fn is_cancelled(&self) -> bool {
        self.0.load(std::sync::atomic::Ordering::Acquire)
    }
    pub fn check(&self) -> Result<(), TransportError> {
        if self.is_cancelled() {
            Err(TransportError::Cancelled)
        } else {
            Ok(())
        }
    }
}

#[derive(Debug, Clone)]
pub struct ReconnectBackoff {
    pub initial: Duration,
    pub maximum: Duration,
    pub attempts: u32,
}
impl Default for ReconnectBackoff {
    fn default() -> Self {
        Self {
            initial: Duration::from_millis(50),
            maximum: Duration::from_secs(5),
            attempts: 0,
        }
    }
}
impl ReconnectBackoff {
    pub fn next_delay(&mut self) -> Duration {
        let shift = self.attempts.min(31);
        self.attempts = self.attempts.saturating_add(1);
        let ms = self
            .initial
            .as_millis()
            .saturating_mul(1u128 << shift)
            .min(self.maximum.as_millis());
        Duration::from_millis(ms as u64)
    }
    pub fn reset(&mut self) {
        self.attempts = 0;
    }
}

#[derive(Debug, Clone)]
pub struct CircuitBreaker {
    failures: u32,
    threshold: u32,
    open_until: Option<std::time::Instant>,
    cooldown: Duration,
}
impl CircuitBreaker {
    pub fn new(threshold: u32) -> Self {
        Self {
            failures: 0,
            threshold: threshold.max(1),
            open_until: None,
            cooldown: Duration::from_secs(5),
        }
    }
    pub fn with_cooldown(threshold: u32, cooldown: Duration) -> Self {
        Self {
            failures: 0,
            threshold: threshold.max(1),
            open_until: None,
            cooldown,
        }
    }
    pub fn allow(&mut self) -> Result<(), TransportError> {
        if let Some(until) = self.open_until {
            if std::time::Instant::now() < until {
                return Err(TransportError::CircuitOpen);
            }
            self.open_until = None;
        }
        Ok(())
    }
    pub fn record_success(&mut self) {
        self.failures = 0;
        self.open_until = None;
    }
    pub fn record_failure(&mut self) {
        self.failures = self.failures.saturating_add(1);
        if self.failures >= self.threshold {
            self.open_until = Some(std::time::Instant::now() + self.cooldown);
        }
    }
    pub fn is_open(&self) -> bool {
        self.open_until
            .map(|x| std::time::Instant::now() < x)
            .unwrap_or(false)
    }
}

#[cfg(unix)]
pub mod unix_transport {
    use super::*;
    use std::net::Shutdown;
    use std::os::unix::net::UnixStream;
    use std::path::Path;
    pub fn connect(
        path: impl AsRef<Path>,
        timeout: Option<Duration>,
    ) -> Result<UnixStream, TransportError> {
        let stream = UnixStream::connect(path).map_err(|e| TransportError::Io(e.to_string()))?;
        if let Some(t) = timeout {
            stream
                .set_read_timeout(Some(t))
                .map_err(|e| TransportError::Io(e.to_string()))?;
            stream
                .set_write_timeout(Some(t))
                .map_err(|e| TransportError::Io(e.to_string()))?;
        }
        Ok(stream)
    }
    pub fn cancel(stream: &UnixStream) -> Result<(), TransportError> {
        stream
            .shutdown(Shutdown::Both)
            .map_err(|e| TransportError::Io(e.to_string()))
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CapabilityHandshake {
    pub version: u16,
    pub nonce: String,
    pub capabilities: Vec<String>,
    pub mac: String,
}

impl CapabilityHandshake {
    /// Builds an authenticated hello. The secret is never serialized directly.
    pub fn authenticated(
        version: u16,
        nonce: impl Into<String>,
        capabilities: Vec<String>,
        secret: &[u8],
    ) -> Self {
        let nonce = nonce.into();
        let mut mac =
            hmac::Hmac::<sha2::Sha256>::new_from_slice(secret).expect("HMAC accepts any key");
        mac.update(version.to_string().as_bytes());
        mac.update(b"\\0");
        mac.update(nonce.as_bytes());
        for capability in &capabilities {
            mac.update(b"\\0");
            mac.update(capability.as_bytes());
        }
        Self {
            version,
            nonce,
            capabilities,
            mac: hex::encode(mac.finalize().into_bytes()),
        }
    }
    pub fn verify(&self, secret: &[u8]) -> bool {
        let expected = Self::authenticated(
            self.version,
            self.nonce.clone(),
            self.capabilities.clone(),
            secret,
        );
        subtle_eq(expected.mac.as_bytes(), self.mac.as_bytes())
    }
}
fn subtle_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// Executes an idempotent operation with bounded attempts and backoff.
pub fn retry_idempotent<T, F>(
    policy: RetryPolicy,
    token: CancellationToken,
    breaker: &mut CircuitBreaker,
    backoff: &mut ReconnectBackoff,
    mut operation: F,
) -> Result<T, TransportError>
where
    F: FnMut() -> Result<T, TransportError>,
{
    let idempotent = policy.class != RetryClass::Never;
    let mut attempt = 0;
    loop {
        token.check()?;
        breaker.allow()?;
        match operation() {
            Ok(value) => {
                breaker.record_success();
                return Ok(value);
            }
            Err(error) => {
                breaker.record_failure();
                attempt += 1;
                if !policy.allows(idempotent, attempt) {
                    return Err(error);
                }
                std::thread::sleep(backoff.next_delay());
            }
        }
    }
}
#[cfg(test)]
mod transport_tests {
    use super::*;
    #[test]
    fn frame_roundtrip_and_bound() {
        let e = Envelope {
            version: 1,
            id: "x".into(),
            method: "m".into(),
            idempotency_key: None,
            deadline_ms: None,
            payload: Value::Null,
            correlation_id: None,
            sequence: None,
        };
        let mut b = Vec::new();
        write_bounded(&mut b, &e).unwrap();
        assert_eq!(read_bounded(&mut b.as_slice(), 512).unwrap(), e);
        assert_eq!(
            read_bounded(&mut b.as_slice(), 1),
            Err(TransportError::TooLarge)
        );
    }
    #[test]
    fn cancellation_and_backoff() {
        let c = CancellationToken::new();
        assert!(c.check().is_ok());
        c.cancel();
        assert_eq!(c.check(), Err(TransportError::Cancelled));
        let mut b = ReconnectBackoff::default();
        assert!(b.next_delay() < b.next_delay());
        b.reset();
        assert_eq!(b.attempts, 0);
    }
    #[test]
    fn circuit_breaker_is_fail_closed() {
        let mut b = CircuitBreaker::new(2);
        assert!(b.allow().is_ok());
        b.record_failure();
        assert!(!b.is_open());
        b.record_failure();
        assert_eq!(b.allow(), Err(TransportError::CircuitOpen));
        b.record_success();
        assert!(!b.is_open());
    }
}
