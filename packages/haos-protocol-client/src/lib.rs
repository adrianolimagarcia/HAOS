use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::time::Duration;
use thiserror::Error;

pub const MAX_ENVELOPE_BYTES: usize = 1024 * 1024;
pub const MAX_METHOD_BYTES: usize = 256;
pub const MAX_IDEMPOTENCY_BYTES: usize = 256;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ProtocolError { #[error("envelope exceeds size limit")] TooLarge, #[error("invalid envelope: {0}")] Invalid(&'static str), #[error("deadline must be positive")] InvalidDeadline }

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Envelope { pub version: u16, pub id: String, pub method: String, #[serde(default)] pub idempotency_key: Option<String>, #[serde(default)] pub deadline_ms: Option<u64>, #[serde(default)] pub payload: Value }

impl Envelope {
 pub fn validate(&self) -> Result<(), ProtocolError> {
  if self.version == 0 { return Err(ProtocolError::Invalid("version")); }
  if self.id.is_empty() || self.id.len() > MAX_IDEMPOTENCY_BYTES { return Err(ProtocolError::Invalid("id")); }
  if self.method.is_empty() || self.method.len() > MAX_METHOD_BYTES || !self.method.bytes().all(|b| b.is_ascii_alphanumeric() || b==b'.' || b==b'_' || b==b'-') { return Err(ProtocolError::Invalid("method")); }
  if let Some(k)=&self.idempotency_key { if k.is_empty() || k.len()>MAX_IDEMPOTENCY_BYTES { return Err(ProtocolError::Invalid("idempotency_key")); } }
  if self.deadline_ms == Some(0) { return Err(ProtocolError::InvalidDeadline); }
  Ok(())
 }
 pub fn encode(&self) -> Result<Vec<u8>, ProtocolError> { self.validate()?; let bytes=serde_json::to_vec(self).map_err(|_|ProtocolError::Invalid("serialization"))?; if bytes.len()>MAX_ENVELOPE_BYTES {return Err(ProtocolError::TooLarge)} Ok(bytes) }
 pub fn timeout(&self) -> Option<Duration> { self.deadline_ms.map(Duration::from_millis) }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)] pub enum RetryClass { Never, IdempotentOnly, AlwaysSafe }
#[derive(Debug, Clone, Copy, PartialEq, Eq)] pub struct RetryPolicy { pub class: RetryClass, pub max_attempts: u32 }
impl RetryPolicy { pub fn allows(&self, idempotent: bool, attempt: u32) -> bool { attempt < self.max_attempts && match self.class { RetryClass::Never=>false, RetryClass::IdempotentOnly=>idempotent, RetryClass::AlwaysSafe=>true } } }

#[cfg(test)]
mod tests { use super::*; fn env()->Envelope{Envelope{version:1,id:"1".into(),method:"mcp.call".into(),idempotency_key:Some("k".into()),deadline_ms:Some(100),payload:serde_json::json!({"x":1})}}
 #[test] fn validates_and_encodes(){let e=env(); assert!(e.validate().is_ok()); assert_eq!(e.timeout(),Some(Duration::from_millis(100))); assert!(!e.encode().unwrap().is_empty());}
 #[test] fn rejects_bad_method(){let mut e=env();e.method="bad space".into();assert_eq!(e.validate(),Err(ProtocolError::Invalid("method")));}
 #[test] fn rejects_zero_deadline(){let mut e=env();e.deadline_ms=Some(0);assert_eq!(e.validate(),Err(ProtocolError::InvalidDeadline));}
 #[test] fn retry_is_idempotency_aware(){let p=RetryPolicy{class:RetryClass::IdempotentOnly,max_attempts:3};assert!(p.allows(true,0));assert!(!p.allows(false,0));assert!(!p.allows(true,3));}
}
