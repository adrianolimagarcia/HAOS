use serde::{Deserialize, Serialize};
use std::collections::HashMap;

use crate::crypto::now_timestamp;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RelationshipEdge {
    pub id: String,
    pub from_bot: String,
    pub to_bot: String,
    pub relation_type: String, // e.g. "trusts", "collaborates_with", "advises", "conflicts_with"
    #[serde(default = "default_weight")]
    pub weight: f64,
    #[serde(default = "now_timestamp")]
    pub valid_from: f64,
    #[serde(default)]
    pub valid_to: Option<f64>,
    #[serde(default)]
    pub evidence_refs: Vec<String>,
    #[serde(default = "default_version")]
    pub schema_version: u32,
    #[serde(default)]
    pub metadata: serde_json::Value,
}

fn default_weight() -> f64 {
    1.0
}
fn default_version() -> u32 {
    1
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReputationEvent {
    pub id: String,
    pub subject_bot: String,
    pub domain: String, // e.g. "architecture", "security", "code_quality", "consensus"
    pub evidence_ref: String,
    pub outcome: String, // "success", "failure", "neutral"
    pub delta_hint: f64,
    pub actor_bot: Option<String>,
    #[serde(default = "now_timestamp")]
    pub occurred_at: f64,
    #[serde(default = "default_version")]
    pub schema_version: u32,
    #[serde(default)]
    pub metadata: serde_json::Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DomainScore {
    pub domain: String,
    pub score: f64,
    pub confidence: f64,
    pub evidence_count: u32,
    pub last_updated: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReputationVector {
    pub bot_id: String,
    pub domains: HashMap<String, DomainScore>,
    pub overall_score: f64,
    pub evidence_count: u32,
    pub as_of: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RoleAssignment {
    pub id: String,
    pub council_session_id: String,
    pub bot_id: String,
    pub role: String, // "moderator", "advocate", "skeptic", "synthesizer"
    pub rationale: String,
    #[serde(default = "now_timestamp")]
    pub assigned_at: f64,
    #[serde(default)]
    pub expires_at: Option<f64>,
    #[serde(default)]
    pub metadata: serde_json::Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CollaborationRecord {
    pub id: String,
    pub participants: Vec<String>,
    pub task_ref: String,
    pub outcome_ref: String,
    pub reviewer_refs: Vec<String>,
    #[serde(default = "now_timestamp")]
    pub recorded_at: f64,
    #[serde(default)]
    pub score: Option<f64>,
    #[serde(default)]
    pub metadata: serde_json::Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum DebatePhase {
    IndependentAnalysis,
    CrossExamination,
    Rebuttal,
    Synthesis,
    DissentRecorded,
    Completed,
}

impl DebatePhase {
    pub fn as_str(&self) -> &'static str {
        match self {
            DebatePhase::IndependentAnalysis => "independent_analysis",
            DebatePhase::CrossExamination => "cross_examination",
            DebatePhase::Rebuttal => "rebuttal",
            DebatePhase::Synthesis => "synthesis",
            DebatePhase::DissentRecorded => "dissent_recorded",
            DebatePhase::Completed => "completed",
        }
    }

    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "independent_analysis" => Some(DebatePhase::IndependentAnalysis),
            "cross_examination" => Some(DebatePhase::CrossExamination),
            "rebuttal" => Some(DebatePhase::Rebuttal),
            "synthesis" => Some(DebatePhase::Synthesis),
            "dissent_recorded" => Some(DebatePhase::DissentRecorded),
            "completed" => Some(DebatePhase::Completed),
            _ => None,
        }
    }
}
