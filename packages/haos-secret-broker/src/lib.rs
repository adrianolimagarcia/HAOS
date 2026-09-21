use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

pub const MAX_FRAME: usize = 64 * 1024;
pub const MAX_FIELD: usize = 256;
pub const MAX_VALUE: usize = 16 * 1024;
pub const MAX_LEASE_TTL_MS: u64 = 5 * 60 * 1000;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RequestContext {
    pub request_id: String,
    pub purpose: String,
    pub deadline_ms: u64,
    pub capability: String,
}
impl RequestContext {
    pub fn validate(&self) -> Result<(), Error> {
        if !valid_field(&self.request_id)
            || !valid_field(&self.purpose)
            || !valid_field(&self.capability)
            || self.deadline_ms < now_ms()
        {
            Err(Error::Invalid)
        } else {
            Ok(())
        }
    }
}

/// Legacy wire shape retained only for migration. It is never accepted by Broker::handle.
#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Request {
    Get {
        profile: String,
        name: String,
    },
    Set {
        profile: String,
        name: String,
        value: String,
    },
    Delete {
        profile: String,
        name: String,
    },
}

#[derive(Debug, Serialize, Deserialize)]
pub enum SecureOperation {
    Acquire {
        profile: String,
        name: String,
        ttl_ms: u64,
    },
    Read {
        lease: String,
    },
    Release {
        lease: String,
    },
    Revoke {
        lease: String,
    },
}
#[derive(Debug, Serialize, Deserialize)]
pub struct SecureRequest {
    pub context: RequestContext,
    pub operation: SecureOperation,
}
#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Lease {
    pub token: String,
    pub expires_at_ms: u64,
}
#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub enum Response {
    Ok { value: Option<String> },
    Error { code: String, message: String },
    Lease { lease: Lease },
    Secret { lease: Lease, value: Vec<u8> },
}

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("invalid request")]
    Invalid,
    #[error("frame too large")]
    TooLarge,
    #[error("unauthorized")]
    Unauthorized,
    #[error("backend error")]
    Backend,
    #[error("expired lease")]
    Expired,
}
pub trait Authorizer: Send + Sync {
    fn authorize(&self, profile: &str, op: &str, name: &str) -> bool;
}
/// Optional audit sink. Implementations must record metadata only, never values or tokens.
pub trait AuditSink: Send + Sync {
    fn record(&self, profile: &str, operation: &str, outcome: &str);
}
#[derive(Clone, Default)]
pub struct InMemoryBackend {
    data: Arc<Mutex<HashMap<(String, String), Vec<u8>>>>,
}
impl InMemoryBackend {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn put(&self, profile: &str, name: &str, value: &[u8]) -> Result<(), Error> {
        if !valid_field(profile) || !valid_field(name) || value.len() > MAX_VALUE {
            return Err(Error::Invalid);
        };
        self.data
            .lock()
            .map_err(|_| Error::Backend)?
            .insert((profile.into(), name.into()), value.to_vec());
        Ok(())
    }
}

struct LeaseState {
    profile: String,
    name: String,
    expires: u64,
    owner: String,
    revoked: bool,
}
pub struct Broker<A: Authorizer> {
    backend: InMemoryBackend,
    authorizer: A,
    audit: Option<Arc<dyn AuditSink>>,
    leases: Mutex<HashMap<String, LeaseState>>,
}
impl<A: Authorizer> Broker<A> {
    pub fn new(backend: InMemoryBackend, authorizer: A) -> Self {
        Self {
            backend,
            authorizer,
            audit: None,
            leases: Mutex::new(HashMap::new()),
        }
    }
    pub fn with_audit(mut self, audit: Arc<dyn AuditSink>) -> Self {
        self.audit = Some(audit);
        self
    }
    /// Legacy operations are rejected; callers must use SecureRequest with capability context.
    pub fn handle(&self, _req: Request) -> Response {
        Response::Error {
            code: "legacy_api_disabled".into(),
            message: "secure request context required".into(),
        }
    }
    pub fn handle_secure(&self, req: SecureRequest) -> Response {
        if req.context.validate().is_err() {
            return self.fail("invalid", "invalid context");
        }
        let now = now_ms();
        let (op, profile, name, lease_token) = match &req.operation {
            SecureOperation::Acquire { profile, name, .. } => {
                ("acquire", profile.as_str(), name.as_str(), None)
            }
            SecureOperation::Read { lease } => ("read", "", "", Some(lease.as_str())),
            SecureOperation::Release { lease } => ("release", "", "", Some(lease.as_str())),
            SecureOperation::Revoke { lease } => ("revoke", "", "", Some(lease.as_str())),
        };
        if op == "acquire" && (!valid_field(profile) || !valid_field(name)) {
            return self.fail("invalid", "invalid field");
        }
        let (profile, name) = if let Some(token) = lease_token {
            let l = self.leases.lock().unwrap();
            let Some(s) = l.get(token) else {
                return self.fail("expired", "invalid lease");
            };
            if s.owner != req.context.capability {
                return self.fail("unauthorized", "lease owner mismatch");
            };
            if s.revoked || s.expires < now {
                return self.fail("expired", "expired lease");
            };
            (s.profile.clone(), s.name.clone())
        } else {
            (profile.into(), name.into())
        };
        if !self.authorizer.authorize(&profile, op, &name) {
            return self.fail("unauthorized", "request denied");
        }
        match req.operation {
            SecureOperation::Acquire {
                profile,
                name,
                ttl_ms,
            } => {
                if ttl_ms == 0 || ttl_ms > MAX_LEASE_TTL_MS {
                    return self.fail("invalid", "invalid ttl");
                };
                let token = random_token();
                let expires = now.saturating_add(ttl_ms);
                self.leases.lock().unwrap().insert(
                    token.clone(),
                    LeaseState {
                        profile: profile.clone(),
                        name: name.clone(),
                        expires,
                        owner: req.context.capability.clone(),
                        revoked: false,
                    },
                );
                self.audit(&profile, "acquire", "ok");
                Response::Lease {
                    lease: Lease {
                        token,
                        expires_at_ms: expires,
                    },
                }
            }
            SecureOperation::Read { lease } => {
                let state = self.leases.lock().unwrap();
                let Some(s) = state.get(&lease) else {
                    return self.fail("expired", "invalid lease");
                };
                let mut value = self
                    .backend
                    .data
                    .lock()
                    .unwrap()
                    .get(&(s.profile.clone(), s.name.clone()))
                    .cloned()
                    .unwrap_or_default();
                let out = value.clone();
                value.fill(0);
                self.audit(&s.profile, "read", "ok");
                Response::Secret {
                    lease: Lease {
                        token: lease,
                        expires_at_ms: s.expires,
                    },
                    value: out,
                }
            }
            SecureOperation::Release { lease } => {
                self.leases.lock().unwrap().remove(&lease);
                self.audit(&profile, "release", "ok");
                Response::Ok { value: None }
            }
            SecureOperation::Revoke { lease } => {
                if let Some(s) = self.leases.lock().unwrap().get_mut(&lease) {
                    s.revoked = true;
                }
                self.audit(&profile, "revoke", "ok");
                Response::Ok { value: None }
            }
        }
    }
    fn audit(&self, p: &str, o: &str, r: &str) {
        if let Some(a) = &self.audit {
            a.record(p, o, r)
        }
    }
    fn fail(&self, c: &str, m: &str) -> Response {
        Response::Error {
            code: c.into(),
            message: m.into(),
        }
    }
}
fn valid_field(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= MAX_FIELD
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
}
fn random_token() -> String {
    let mut b = [0u8; 32];
    if std::fs::File::open("/dev/urandom")
        .and_then(|mut f| std::io::Read::read_exact(&mut f, &mut b))
        .is_err()
    {
        let n = now_ms();
        b[..8].copy_from_slice(&n.to_le_bytes());
    }
    b.iter().map(|x| format!("{x:02x}")).collect()
}
fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
pub fn encode(req: &Request) -> Result<Vec<u8>, Error> {
    let b = serde_json::to_vec(req).map_err(|_| Error::Invalid)?;
    if b.len() > MAX_FRAME {
        return Err(Error::TooLarge);
    }
    let mut o = (b.len() as u32).to_be_bytes().to_vec();
    o.extend(b);
    Ok(o)
}
pub fn decode(frame: &[u8]) -> Result<Request, Error> {
    if frame.len() < 4 {
        return Err(Error::Invalid);
    }
    let n = u32::from_be_bytes(frame[..4].try_into().unwrap()) as usize;
    if n > MAX_FRAME || n != frame.len() - 4 {
        return Err(Error::Invalid);
    }
    serde_json::from_slice(&frame[4..]).map_err(|_| Error::Invalid)
}
#[cfg(test)]
mod tests {
    use super::*;
    struct Allow;
    impl Authorizer for Allow {
        fn authorize(&self, _: &str, _: &str, _: &str) -> bool {
            true
        }
    }
    fn ctx() -> RequestContext {
        RequestContext {
            request_id: "r".into(),
            purpose: "p".into(),
            deadline_ms: now_ms() + 1000,
            capability: "owner".into(),
        }
    }
    #[test]
    fn lease_and_owner() {
        let b = Broker::new(InMemoryBackend::new(), Allow);
        b.backend.put("a", "x", b"secret").unwrap();
        let r = b.handle_secure(SecureRequest {
            context: ctx(),
            operation: SecureOperation::Acquire {
                profile: "a".into(),
                name: "x".into(),
                ttl_ms: 100,
            },
        });
        let token = match r {
            Response::Lease { lease } => lease.token,
            _ => panic!(),
        };
        let mut other = ctx();
        other.capability = "other".into();
        assert!(matches!(
            b.handle_secure(SecureRequest {
                context: other,
                operation: SecureOperation::Read { lease: token }
            }),
            Response::Error { code, .. }
        ));
    }
    #[test]
    fn ttl_and_fields() {
        let b = Broker::new(InMemoryBackend::new(), Allow);
        assert!(matches!(
            b.handle_secure(SecureRequest {
                context: ctx(),
                operation: SecureOperation::Acquire {
                    profile: "bad/".into(),
                    name: "x".into(),
                    ttl_ms: MAX_LEASE_TTL_MS + 1
                }
            }),
            Response::Error { .. }
        ));
    }
    #[test]
    fn legacy_disabled() {
        let b = Broker::new(InMemoryBackend::new(), Allow);
        assert!(matches!(
            b.handle(Request::Get {
                profile: "a".into(),
                name: "x".into()
            }),
            Response::Error { code, .. }
        ));
    }
}
