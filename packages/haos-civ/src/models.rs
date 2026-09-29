use serde::{Deserialize, Serialize};
use std::collections::HashMap;

use crate::crypto::{compute_bundle_hash, compute_sha256, now_timestamp};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BotIdentitySpec {
    #[serde(default = "default_soul_file")]
    pub soul: String,
    #[serde(default = "default_identity_file")]
    pub identity: String,
    #[serde(default = "default_values_file")]
    pub values: String,
    #[serde(default = "default_version")]
    pub version: u32,
}

fn default_soul_file() -> String {
    "SOUL.md".to_string()
}
fn default_identity_file() -> String {
    "IDENTITY.md".to_string()
}
fn default_values_file() -> String {
    "VALUES.md".to_string()
}
fn default_version() -> u32 {
    1
}

impl Default for BotIdentitySpec {
    fn default() -> Self {
        Self {
            soul: default_soul_file(),
            identity: default_identity_file(),
            values: default_values_file(),
            version: default_version(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BotIdentityBundle {
    pub bot_id: String,
    #[serde(default = "default_version")]
    pub identity_version: u32,
    #[serde(default)]
    pub soul: String,
    #[serde(default)]
    pub identity: String,
    #[serde(default)]
    pub values: String,
    #[serde(default)]
    pub soul_hash: String,
    #[serde(default)]
    pub identity_hash: String,
    #[serde(default)]
    pub values_hash: String,
    #[serde(default)]
    pub bundle_hash: String,
    #[serde(default = "default_version")]
    pub schema_version: u32,
    #[serde(default)]
    pub metadata: serde_json::Value,
}

impl BotIdentityBundle {
    pub fn new(
        bot_id: impl Into<String>,
        identity_version: u32,
        soul: impl Into<String>,
        identity: impl Into<String>,
        values: impl Into<String>,
    ) -> Result<Self, &'static str> {
        let bot_id = bot_id.into();
        if bot_id.trim().is_empty() {
            return Err("bot_id is required");
        }
        let soul = soul.into();
        let identity = identity.into();
        let values = values.into();

        let soul_hash = compute_sha256(&soul);
        let identity_hash = compute_sha256(&identity);
        let values_hash = compute_sha256(&values);
        let bundle_hash = compute_bundle_hash(&soul, &identity, &values);

        Ok(Self {
            bot_id,
            identity_version,
            soul,
            identity,
            values,
            soul_hash,
            identity_hash,
            values_hash,
            bundle_hash,
            schema_version: 1,
            metadata: serde_json::Value::Object(serde_json::Map::new()),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct IdentityVersion {
    pub id: String,
    pub bot_id: String,
    pub version: u32,
    pub bundle_hash: String,
    #[serde(default)]
    pub parent_id: Option<String>,
    #[serde(default)]
    pub bundle: Option<BotIdentityBundle>,
    #[serde(default = "default_status_active")]
    pub status: String,
    #[serde(default = "now_timestamp")]
    pub created_at: f64,
    #[serde(default = "default_version")]
    pub schema_version: u32,
    #[serde(default)]
    pub metadata: serde_json::Value,
}

fn default_status_active() -> String {
    "active".to_string()
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LeafIdentitySnapshot {
    pub leaf_id: String,
    pub parent_bot_id: String,
    pub identity_version_id: String,
    #[serde(default = "default_version")]
    pub bot_identity_version: u32,
    #[serde(default)]
    pub identity_bundle_hash: String,
    #[serde(default)]
    pub temporary_soul: String,
    #[serde(default)]
    pub temporary_soul_hash: String,
    #[serde(default)]
    pub prompt_hash: String,
    #[serde(default)]
    pub toolset_hash: String,
    #[serde(default)]
    pub memory_snapshot_ref: Option<String>,
    #[serde(default)]
    pub council_id: Option<String>,
    #[serde(default)]
    pub council_session_id: Option<String>,
    #[serde(default)]
    pub model: String,
    #[serde(default = "now_timestamp")]
    pub created_at: f64,
    #[serde(default)]
    pub expires_at: Option<f64>,
    #[serde(default)]
    pub causation_id: Option<String>,
    #[serde(default)]
    pub correlation_id: Option<String>,
    #[serde(default = "default_version")]
    pub schema_version: u32,
    #[serde(default)]
    pub metadata: serde_json::Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CouncilSpec {
    pub id: String,
    pub purpose: String,
    #[serde(default)]
    pub members: Vec<String>,
    #[serde(default)]
    pub roles: HashMap<String, String>,
    #[serde(default = "default_decision_mode")]
    pub decision_mode: String,
    #[serde(default)]
    pub budget: serde_json::Value,
    #[serde(default)]
    pub policy_ref: Option<String>,
    #[serde(default)]
    pub rules: Vec<String>,
    #[serde(default = "default_version")]
    pub version: u32,
    #[serde(default = "default_version")]
    pub schema_version: u32,
    #[serde(default)]
    pub metadata: serde_json::Value,
}

fn default_decision_mode() -> String {
    "consensus_with_dissent".to_string()
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CouncilBudget {
    #[serde(default = "default_max_rounds")]
    pub max_rounds: u32,
    #[serde(default = "default_max_turns")]
    pub max_turns: u32,
    #[serde(default = "default_max_tokens")]
    pub max_tokens: u64,
    #[serde(default = "default_max_cost")]
    pub max_cost_usd: f64,
    #[serde(default = "default_timeout_seconds")]
    pub timeout_seconds: f64,
    #[serde(default = "default_max_members")]
    pub max_members: u32,
    #[serde(default)]
    pub consumed_rounds: u32,
    #[serde(default)]
    pub consumed_turns: u32,
    #[serde(default)]
    pub consumed_tokens: u64,
    #[serde(default)]
    pub consumed_cost_usd: f64,
    #[serde(default = "now_timestamp")]
    pub started_at: f64,
}

fn default_max_rounds() -> u32 {
    3
}
fn default_max_turns() -> u32 {
    12
}
fn default_max_tokens() -> u64 {
    150_000
}
fn default_max_cost() -> f64 {
    5.0
}
fn default_timeout_seconds() -> f64 {
    300.0
}
fn default_max_members() -> u32 {
    7
}

impl Default for CouncilBudget {
    fn default() -> Self {
        Self {
            max_rounds: default_max_rounds(),
            max_turns: default_max_turns(),
            max_tokens: default_max_tokens(),
            max_cost_usd: default_max_cost(),
            timeout_seconds: default_timeout_seconds(),
            max_members: default_max_members(),
            consumed_rounds: 0,
            consumed_turns: 0,
            consumed_tokens: 0,
            consumed_cost_usd: 0.0,
            started_at: now_timestamp(),
        }
    }
}

impl CouncilBudget {
    pub fn reserve_round(&mut self) -> Result<(), &'static str> {
        if self.consumed_rounds >= self.max_rounds {
            return Err("max_rounds exhausted");
        }
        self.consumed_rounds += 1;
        Ok(())
    }

    pub fn reserve_turn(&mut self) -> Result<(), &'static str> {
        if self.consumed_turns >= self.max_turns {
            return Err("max_turns exhausted");
        }
        self.consumed_turns += 1;
        Ok(())
    }

    pub fn charge(&mut self, tokens: u64, cost_usd: f64) -> Result<(), &'static str> {
        self.consumed_tokens += tokens;
        self.consumed_cost_usd += cost_usd;
        if self.consumed_tokens > self.max_tokens {
            return Err("max_tokens exhausted");
        }
        if self.consumed_cost_usd > self.max_cost_usd {
            return Err("max_cost_usd exhausted");
        }
        Ok(())
    }

    pub fn check_timeout(&self) -> Result<(), &'static str> {
        if (now_timestamp() - self.started_at) > self.timeout_seconds {
            return Err("timeout_seconds exceeded");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DebateTurn {
    pub bot_id: String,
    pub round_number: u32,
    pub phase: String,
    pub position: String,
    #[serde(default)]
    pub critique_of: Option<String>,
    #[serde(default)]
    pub evidence_refs: Vec<String>,
    #[serde(default = "now_timestamp")]
    pub timestamp: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CouncilSession {
    pub session_id: String,
    pub council_id: String,
    pub objective: String,
    #[serde(default)]
    pub members: Vec<String>,
    #[serde(default = "default_session_phase")]
    pub phase: String,
    #[serde(default)]
    pub positions: HashMap<String, serde_json::Value>,
    #[serde(default)]
    pub dissent: HashMap<String, String>,
    #[serde(default)]
    pub leaf_runs: Vec<String>,
    #[serde(default)]
    pub decision_id: Option<String>,
    #[serde(default)]
    pub command_id: Option<String>,
    #[serde(default)]
    pub budget: Option<CouncilBudget>,
    #[serde(default)]
    pub debate_turns: Vec<DebateTurn>,
    #[serde(default)]
    pub revision: u64,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default = "now_timestamp")]
    pub created_at: f64,
    #[serde(default = "now_timestamp")]
    pub updated_at: f64,
    #[serde(default)]
    pub correlation_id: Option<String>,
    #[serde(default)]
    pub metadata: serde_json::Value,
}

fn default_session_phase() -> String {
    "created".to_string()
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DecisionRecord {
    pub id: String,
    pub council_id: String,
    pub council_session_id: String,
    pub objective: String,
    #[serde(default)]
    pub participants: Vec<String>,
    #[serde(default)]
    pub identity_version_refs: HashMap<String, String>,
    #[serde(default)]
    pub leaf_refs: Vec<String>,
    #[serde(default)]
    pub evidence_refs: Vec<String>,
    #[serde(default)]
    pub positions: HashMap<String, serde_json::Value>,
    #[serde(default)]
    pub synthesis: String,
    #[serde(default)]
    pub dissent: HashMap<String, String>,
    #[serde(default)]
    pub decision: String,
    #[serde(default = "default_confidence")]
    pub confidence: f64,
    #[serde(default)]
    pub action_refs: Vec<String>,
    #[serde(default = "default_true")]
    pub policy_verified: bool,
    #[serde(default = "now_timestamp")]
    pub created_at: f64,
    #[serde(default)]
    pub correlation_id: Option<String>,
    #[serde(default = "default_version")]
    pub schema_version: u32,
    #[serde(default)]
    pub metadata: serde_json::Value,
}

fn default_confidence() -> f64 {
    1.0
}
fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CivEvent {
    pub event_id: String,
    #[serde(default)]
    pub seq: i64,
    pub name: String,
    #[serde(default)]
    pub trace_id: Option<String>,
    #[serde(default)]
    pub correlation_id: Option<String>,
    #[serde(default)]
    pub causation_id: Option<String>,
    #[serde(default = "default_trust_level")]
    pub trust_level: String,
    #[serde(default = "default_version")]
    pub schema_version: u32,
    #[serde(default = "now_timestamp")]
    pub timestamp: f64,
    pub payload: serde_json::Value,
}

fn default_trust_level() -> String {
    "local_system".to_string()
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CivGraphNode {
    pub id: String,
    pub kind: String,
    pub label: String,
    pub status: String,
    #[serde(default)]
    pub bot_id: Option<String>,
    #[serde(default)]
    pub version: Option<u32>,
    #[serde(default)]
    pub score: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CivGraphEdge {
    pub id: String,
    pub source: String,
    pub target: String,
    pub kind: String,
    pub directed: bool,
    #[serde(default)]
    pub weight: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CivGraphDTO {
    #[serde(default = "default_version")]
    pub schema_version: u32,
    #[serde(default)]
    pub nodes: Vec<CivGraphNode>,
    #[serde(default)]
    pub edges: Vec<CivGraphEdge>,
    #[serde(default)]
    pub cursor: u64,
    #[serde(default)]
    pub counts: HashMap<String, usize>,
}

