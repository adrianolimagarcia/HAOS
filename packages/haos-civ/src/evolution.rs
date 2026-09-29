use serde::{Deserialize, Serialize};
use std::collections::HashMap;

use crate::crypto::{generate_id, now_timestamp};
use crate::event_store::CivEventStore;
use crate::models::CivEvent;

pub const RISK_LOW: &str = "low";
pub const RISK_MEDIUM: &str = "medium";
pub const RISK_HIGH: &str = "high";
pub const RISK_IDENTITY_CRITICAL: &str = "identity-critical";

pub const STATUS_DRAFT: &str = "draft";
pub const STATUS_REVIEW: &str = "review";
pub const STATUS_APPROVED: &str = "approved";
pub const STATUS_CANARY: &str = "canary";
pub const STATUS_PROMOTED: &str = "promoted";
pub const STATUS_REJECTED: &str = "rejected";
pub const STATUS_ROLLED_BACK: &str = "rolled_back";

pub const EVENT_EXPERIENCE_RECORDED: &str = "civ.evolution.experience_recorded";
pub const EVENT_PROPOSAL_CREATED: &str = "civ.evolution.proposal_created";
pub const EVENT_PROPOSAL_UPDATED: &str = "civ.evolution.proposal_updated";
pub const EVENT_PROPOSAL_PROMOTED: &str = "civ.evolution.proposal_promoted";
pub const EVENT_PROPOSAL_ROLLED_BACK: &str = "civ.evolution.proposal_rolled_back";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ExperienceEvent {
    pub id: String,
    pub bot_id: String,
    pub event_type: String,
    pub domain: String,
    pub summary: String,
    pub success: bool,
    pub payload: HashMap<String, serde_json::Value>,
    pub occurred_at: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct EvolutionProposal {
    pub id: String,
    pub bot_id: String,
    pub base_version_hash: String,
    pub risk_class: String,
    pub status: String,
    pub proposed_soul_patch: Option<String>,
    pub proposed_identity_patch: Option<String>,
    pub proposed_values_patch: Option<String>,
    pub rationale: String,
    pub evidence_refs: Vec<String>,
    pub created_at: f64,
    pub updated_at: f64,
}

#[derive(Debug, thiserror::Error)]
pub enum EvolutionError {
    #[error("Store error: {0}")]
    Store(#[from] crate::event_store::EventStoreError),
    #[error("Proposal not found: {0}")]
    NotFound(String),
    #[error("Stale base version: expected {expected}, actual current is {actual}")]
    StaleBaseVersion { expected: String, actual: String },
    #[error("Invalid status transition from {from} to {to}")]
    InvalidTransition { from: String, to: String },
    #[error("Identity critical change requires council approval")]
    PolicyViolation(String),
}

pub struct EvolutionManager {
    store: CivEventStore,
}

impl EvolutionManager {
    pub fn new(store: CivEventStore) -> Self {
        Self { store }
    }

    pub fn record_experience(
        &self,
        bot_id: &str,
        event_type: &str,
        domain: &str,
        summary: &str,
        success: bool,
        payload: HashMap<String, serde_json::Value>,
    ) -> Result<ExperienceEvent, EvolutionError> {
        let exp = ExperienceEvent {
            id: generate_id("exp"),
            bot_id: bot_id.to_string(),
            event_type: event_type.to_string(),
            domain: domain.to_string(),
            summary: summary.to_string(),
            success,
            payload,
            occurred_at: now_timestamp(),
        };

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_EXPERIENCE_RECORDED.to_string(),
            trace_id: None,
            correlation_id: None,
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: exp.occurred_at,
            payload: serde_json::to_value(&exp).unwrap_or_default(),
        };
        self.store.append(&ev)?;
        Ok(exp)
    }

    pub fn create_proposal(
        &self,
        bot_id: &str,
        base_version_hash: &str,
        risk_class: &str,
        proposed_soul_patch: Option<String>,
        proposed_identity_patch: Option<String>,
        proposed_values_patch: Option<String>,
        rationale: &str,
        evidence_refs: Vec<String>,
    ) -> Result<EvolutionProposal, EvolutionError> {
        let now = now_timestamp();
        let proposal = EvolutionProposal {
            id: generate_id("prop"),
            bot_id: bot_id.to_string(),
            base_version_hash: base_version_hash.to_string(),
            risk_class: risk_class.to_string(),
            status: STATUS_DRAFT.to_string(),
            proposed_soul_patch,
            proposed_identity_patch,
            proposed_values_patch,
            rationale: rationale.to_string(),
            evidence_refs,
            created_at: now,
            updated_at: now,
        };

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_PROPOSAL_CREATED.to_string(),
            trace_id: None,
            correlation_id: None,
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now,
            payload: serde_json::to_value(&proposal).unwrap_or_default(),
        };
        self.store.append(&ev)?;
        Ok(proposal)
    }

    pub fn get_proposals(&self, bot_id: Option<&str>) -> Result<Vec<EvolutionProposal>, EvolutionError> {
        let mut proposals: HashMap<String, EvolutionProposal> = HashMap::new();
        let events = self.store.get_all()?;

        for e in events {
            if e.name == EVENT_PROPOSAL_CREATED || e.name == EVENT_PROPOSAL_UPDATED {
                if let Ok(p) = serde_json::from_value::<EvolutionProposal>(e.payload) {
                    proposals.insert(p.id.clone(), p);
                }
            } else if e.name == EVENT_PROPOSAL_PROMOTED {
                if let Some(id) = e.payload.get("proposal_id").and_then(|v| v.as_str()) {
                    if let Some(p) = proposals.get_mut(id) {
                        p.status = STATUS_PROMOTED.to_string();
                        p.updated_at = e.timestamp;
                    }
                }
            } else if e.name == EVENT_PROPOSAL_ROLLED_BACK {
                if let Some(id) = e.payload.get("proposal_id").and_then(|v| v.as_str()) {
                    if let Some(p) = proposals.get_mut(id) {
                        p.status = STATUS_ROLLED_BACK.to_string();
                        p.updated_at = e.timestamp;
                    }
                }
            }
        }

        let mut list: Vec<EvolutionProposal> = proposals.into_values().collect();
        if let Some(bid) = bot_id {
            list.retain(|p| p.bot_id == bid);
        }
        list.sort_by(|a, b| b.created_at.partial_cmp(&a.created_at).unwrap());
        Ok(list)
    }

    pub fn update_status(
        &self,
        proposal_id: &str,
        new_status: &str,
        current_bot_version_hash: &str,
    ) -> Result<EvolutionProposal, EvolutionError> {
        let proposals = self.get_proposals(None)?;
        let mut target = proposals
            .into_iter()
            .find(|p| p.id == proposal_id)
            .ok_or_else(|| EvolutionError::NotFound(proposal_id.to_string()))?;

        // Invariant: Stale base version cannot be approved or promoted
        if (new_status == STATUS_APPROVED || new_status == STATUS_PROMOTED || new_status == STATUS_CANARY)
            && target.base_version_hash != current_bot_version_hash
        {
            return Err(EvolutionError::StaleBaseVersion {
                expected: target.base_version_hash.clone(),
                actual: current_bot_version_hash.to_string(),
            });
        }

        target.status = new_status.to_string();
        target.updated_at = now_timestamp();

        let ev_type = match new_status {
            STATUS_PROMOTED => EVENT_PROPOSAL_PROMOTED,
            STATUS_ROLLED_BACK => EVENT_PROPOSAL_ROLLED_BACK,
            _ => EVENT_PROPOSAL_UPDATED,
        };

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: ev_type.to_string(),
            trace_id: None,
            correlation_id: None,
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: target.updated_at,
            payload: serde_json::to_value(&target).unwrap_or_default(),
        };
        self.store.append(&ev)?;
        Ok(target)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_evolution_workflow_and_stale_detection() {
        let store = CivEventStore::in_memory().unwrap();
        let evo = EvolutionManager::new(store);

        let exp = evo
            .record_experience(
                "bot-coder",
                "task_outcome",
                "compiler",
                "Fixed borrow checker error in c_abi",
                true,
                HashMap::new(),
            )
            .unwrap();
        assert_eq!(exp.bot_id, "bot-coder");
        assert!(exp.success);

        let prop = evo
            .create_proposal(
                "bot-coder",
                "hash-v1",
                RISK_LOW,
                Some("Updated coding rules".to_string()),
                None,
                None,
                "Learned to avoid cloning heavy buffers",
                vec![exp.id.clone()],
            )
            .unwrap();

        assert_eq!(prop.status, STATUS_DRAFT);
        assert_eq!(prop.base_version_hash, "hash-v1");

        // Stale detection: current bot version moved to hash-v2
        let stale_err = evo.update_status(&prop.id, STATUS_APPROVED, "hash-v2");
        assert!(matches!(stale_err, Err(EvolutionError::StaleBaseVersion { .. })));

        // Valid approval when base version matches current bot version
        let approved = evo.update_status(&prop.id, STATUS_APPROVED, "hash-v1").unwrap();
        assert_eq!(approved.status, STATUS_APPROVED);

        // Canary promotion
        let canary = evo.update_status(&prop.id, STATUS_CANARY, "hash-v1").unwrap();
        assert_eq!(canary.status, STATUS_CANARY);

        // Promoted
        let promoted = evo.update_status(&prop.id, STATUS_PROMOTED, "hash-v1").unwrap();
        assert_eq!(promoted.status, STATUS_PROMOTED);
    }
}
