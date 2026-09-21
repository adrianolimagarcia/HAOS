//! Unix-socket event bridge protocol: bounded, reconnect-friendly framed messages.
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::io::{self, Read, Write};
use thiserror::Error;

pub const DEFAULT_MAX_FRAME: usize = 1024 * 1024;
/// Maximum UTF-8 byte length for routing identifiers.
pub const MAX_ID_BYTES: usize = 256;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Envelope {
    pub session_id: String,
    pub correlation_id: String,
    pub body: Message,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "type", content = "data")]
pub enum Message {
    Event(serde_json::Value),
    Ack,
    Error { code: String, message: String },
}

#[derive(Debug, Error)]
pub enum ProtocolError {
    #[error("frame exceeds maximum size")]
    TooLarge,
    #[error("invalid {0}")]
    InvalidIdentifier(&'static str),
    #[error("truncated frame")]
    Truncated,
    #[error("invalid JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error("I/O: {0}")]
    Io(#[from] io::Error),
}

pub fn encode_frame(value: &Envelope, max_frame: usize) -> Result<Vec<u8>, ProtocolError> {
    validate_identifier(&value.session_id, "session_id")?;
    validate_identifier(&value.correlation_id, "correlation_id")?;
    let payload = serde_json::to_vec(value)?;
    if payload.len() > max_frame {
        return Err(ProtocolError::TooLarge);
    }
    let len = u32::try_from(payload.len()).map_err(|_| ProtocolError::TooLarge)?;
    let mut out = Vec::with_capacity(4 + payload.len());
    out.extend_from_slice(&len.to_be_bytes());
    out.extend_from_slice(&payload);
    Ok(out)
}

pub fn write_frame<W: Write>(
    w: &mut W,
    value: &Envelope,
    max_frame: usize,
) -> Result<(), ProtocolError> {
    let frame = encode_frame(value, max_frame)?;
    w.write_all(&frame)?;
    w.flush()?;
    Ok(())
}

fn validate_identifier(value: &str, name: &'static str) -> Result<(), ProtocolError> {
    if value.is_empty() || value.len() > MAX_ID_BYTES {
        return Err(ProtocolError::InvalidIdentifier(name));
    }
    Ok(())
}

pub fn read_frame<R: Read>(r: &mut R, max_frame: usize) -> Result<Envelope, ProtocolError> {
    let mut header = [0u8; 4];
    r.read_exact(&mut header).map_err(|e| {
        if e.kind() == io::ErrorKind::UnexpectedEof {
            ProtocolError::Truncated
        } else {
            e.into()
        }
    })?;
    let len = u32::from_be_bytes(header) as usize;
    if len > max_frame {
        return Err(ProtocolError::TooLarge);
    }
    let mut payload = vec![0u8; len];
    r.read_exact(&mut payload).map_err(|e| {
        if e.kind() == io::ErrorKind::UnexpectedEof {
            ProtocolError::Truncated
        } else {
            e.into()
        }
    })?;
    let envelope: Envelope = serde_json::from_slice(&payload)?;
    validate_identifier(&envelope.session_id, "session_id")?;
    validate_identifier(&envelope.correlation_id, "correlation_id")?;
    Ok(envelope)
}

/// Bounded outbound queue. `push` returns false when backpressure must be applied.
pub struct BoundedQueue<T> {
    items: VecDeque<T>,
    capacity: usize,
}
impl<T> BoundedQueue<T> {
    pub fn new(capacity: usize) -> Self {
        Self {
            items: VecDeque::new(),
            capacity,
        }
    }
    pub fn push(&mut self, item: T) -> bool {
        if self.items.len() >= self.capacity {
            false
        } else {
            self.items.push_back(item);
            true
        }
    }
    pub fn pop(&mut self) -> Option<T> {
        self.items.pop_front()
    }
    pub fn len(&self) -> usize {
        self.items.len()
    }
    pub fn is_full(&self) -> bool {
        self.items.len() >= self.capacity
    }
}

#[cfg(unix)]
pub mod unix {
    use super::*;
    use std::os::unix::net::{UnixListener, UnixStream};
    use std::path::Path;
    pub fn connect(path: impl AsRef<Path>) -> io::Result<UnixStream> {
        UnixStream::connect(path)
    }
    pub fn bind(path: impl AsRef<Path>) -> io::Result<UnixListener> {
        UnixListener::bind(path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn env() -> Envelope {
        Envelope {
            session_id: "s".into(),
            correlation_id: "c".into(),
            body: Message::Event(serde_json::json!({"x":1})),
        }
    }
    #[test]
    fn round_trip() {
        let mut b = Vec::new();
        write_frame(&mut b, &env(), 100).unwrap();
        assert_eq!(read_frame(&mut b.as_slice(), 100).unwrap(), env());
    }
    #[test]
    fn rejects_large() {
        assert!(matches!(
            encode_frame(&env(), 1),
            Err(ProtocolError::TooLarge)
        ));
    }
    #[test]
    fn rejects_truncated() {
        assert!(matches!(
            read_frame(&mut &[0, 0, 0, 4][..], 10),
            Err(ProtocolError::Truncated)
        ));
    }
    #[test]
    fn queue_backpressure() {
        let mut q = BoundedQueue::new(1);
        assert!(q.push(1));
        assert!(!q.push(2));
        assert_eq!(q.pop(), Some(1));
    }
}
