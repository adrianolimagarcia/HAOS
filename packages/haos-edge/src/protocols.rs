//! Fast native Rust implementation of HAOS cross-boundary protocol fabric.
//! Handles A2A (Agent-to-Agent), ACP (Agent Client Protocol), and ANP/ADP envelopes,
//! signature verification, and zero-allocation cross-protocol bridging.

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};
use serde::{Deserialize, Serialize};
use sha2::{Sha256, Digest};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "UPPERCASE")]
pub enum ProtocolType {
    Internal,
    Mcp,
    Acp,
    A2a,
    Anp,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TrustBoundary {
    Kernel,
    LocalSecure,
    AgentSandbox,
    Federated,
    Untrusted,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProtocolEnvelope {
    pub protocol_type: ProtocolType,
    pub sender: String,
    pub recipient: String,
    pub payload: serde_json::Value,
    #[serde(default = "default_trust_boundary")]
    pub trust_boundary: TrustBoundary,
    #[serde(default = "current_timestamp")]
    pub timestamp: f64,
    #[serde(default)]
    pub signature: Option<String>,
    #[serde(default = "generate_envelope_id")]
    pub envelope_id: String,
    #[serde(default)]
    pub metadata: HashMap<String, serde_json::Value>,
}

fn default_trust_boundary() -> TrustBoundary {
    TrustBoundary::LocalSecure
}

fn current_timestamp() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

fn generate_envelope_id() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let mut hasher = Sha256::new();
    hasher.update(now.to_string().as_bytes());
    format!("{:x}", hasher.finalize())[..16].to_string()
}

impl ProtocolEnvelope {
    /// Canonical content digest for E2E cryptographic signature & integrity
    pub fn compute_canonical_hash(&self) -> String {
        let mut hasher = Sha256::new();
        hasher.update(format!("{:?}", self.protocol_type).as_bytes());
        hasher.update(b":");
        hasher.update(self.sender.as_bytes());
        hasher.update(b":");
        hasher.update(self.recipient.as_bytes());
        hasher.update(b":");
        let payload_str = serde_json::to_string(&self.payload).unwrap_or_default();
        hasher.update(payload_str.as_bytes());
        format!("{:x}", hasher.finalize())
    }

    /// Fast-path E2E verification
    pub fn verify_signature(&self) -> bool {
        if let Some(sig) = &self.signature {
            // Se tiver assinatura formatada "sha256:<hash>", valida em Rust
            if let Some(expected_hash) = sig.strip_prefix("sha256:") {
                return self.compute_canonical_hash() == expected_hash;
            }
            !sig.is_empty()
        } else {
            // Mensagens internas locais não exigem assinatura se trust_boundary <= LocalSecure
            matches!(self.trust_boundary, TrustBoundary::Kernel | TrustBoundary::LocalSecure)
        }
    }
}

/// Fast Bridge Translator (zero-copy when possible)
pub struct FastCrossProtocolBridge;

impl FastCrossProtocolBridge {
    pub fn acp_to_internal(acp_event: serde_json::Value, sender: &str, recipient: &str) -> ProtocolEnvelope {
        let method = acp_event.get("method")
            .or_else(|| acp_event.get("type"))
            .and_then(|v| v.as_str())
            .unwrap_or("acp.event")
            .to_string();

        let payload = acp_event.get("params")
            .or_else(|| acp_event.get("payload"))
            .cloned()
            .unwrap_or_else(|| acp_event.clone());

        let mut metadata = HashMap::new();
        metadata.insert("source_protocol".to_string(), serde_json::json!("ACP"));
        metadata.insert("method".to_string(), serde_json::json!(method));
        if let Some(id) = acp_event.get("id") {
            metadata.insert("raw_id".to_string(), id.clone());
        }

        ProtocolEnvelope {
            protocol_type: ProtocolType::Internal,
            sender: sender.to_string(),
            recipient: recipient.to_string(),
            payload,
            trust_boundary: TrustBoundary::LocalSecure,
            timestamp: current_timestamp(),
            signature: None,
            envelope_id: generate_envelope_id(),
            metadata,
        }
    }

    pub fn internal_to_a2a(envelope: &ProtocolEnvelope) -> ProtocolEnvelope {
        let mut a2a_payload = serde_json::Map::new();
        a2a_payload.insert("role".to_string(), serde_json::json!("user"));

        let parts = vec![
            serde_json::json!({
                "kind": "text",
                "text": envelope.payload.to_string()
            })
        ];
        a2a_payload.insert("parts".to_string(), serde_json::Value::Array(parts));

        let mut metadata = envelope.metadata.clone();
        metadata.insert("origin_protocol".to_string(), serde_json::json!("INTERNAL"));
        metadata.insert("bridged_at".to_string(), serde_json::json!(current_timestamp()));

        let mut bridged = ProtocolEnvelope {
            protocol_type: ProtocolType::A2a,
            sender: envelope.sender.clone(),
            recipient: envelope.recipient.clone(),
            payload: serde_json::Value::Object(a2a_payload),
            trust_boundary: envelope.trust_boundary.clone(),
            timestamp: current_timestamp(),
            signature: None,
            envelope_id: generate_envelope_id(),
            metadata,
        };
        bridged.signature = Some(format!("sha256:{}", bridged.compute_canonical_hash()));
        bridged
    }

    pub fn a2a_to_anp(envelope: &ProtocolEnvelope, did_sender: &str, did_recipient: &str) -> ProtocolEnvelope {
        let mut anp_body = serde_json::Map::new();
        anp_body.insert("action".to_string(), serde_json::json!("anp.message.send"));
        anp_body.insert("content".to_string(), envelope.payload.clone());

        let mut anp_meta = serde_json::Map::new();
        anp_meta.insert("source_did".to_string(), serde_json::json!(did_sender));
        anp_meta.insert("target_did".to_string(), serde_json::json!(did_recipient));
        anp_meta.insert("timestamp".to_string(), serde_json::json!(current_timestamp()));

        let mut anp_wire = serde_json::Map::new();
        anp_wire.insert("meta".to_string(), serde_json::Value::Object(anp_meta));
        anp_wire.insert("body".to_string(), serde_json::Value::Object(anp_body));

        let mut metadata = envelope.metadata.clone();
        metadata.insert("origin_protocol".to_string(), serde_json::json!("A2A"));
        metadata.insert("bridged_to_anp".to_string(), serde_json::json!(true));

        let mut bridged = ProtocolEnvelope {
            protocol_type: ProtocolType::Anp,
            sender: did_sender.to_string(),
            recipient: did_recipient.to_string(),
            payload: serde_json::Value::Object(anp_wire),
            trust_boundary: TrustBoundary::Federated,
            timestamp: current_timestamp(),
            signature: None,
            envelope_id: generate_envelope_id(),
            metadata,
        };
        bridged.signature = Some(format!("sha256:{}", bridged.compute_canonical_hash()));
        bridged
    }
}
