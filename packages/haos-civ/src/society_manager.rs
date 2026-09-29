use std::collections::HashMap;
use thiserror::Error;

use crate::crypto::{generate_id, now_timestamp};
use crate::event_store::{CivEventStore, EventStoreError};
use crate::models::CivEvent;
use crate::society::{
    CollaborationRecord, DomainScore, RelationshipEdge, ReputationEvent, ReputationVector,
    RoleAssignment,
};

const EVENT_RELATION_ESTABLISHED: &str = "civ.society.relationship-established";
const EVENT_REPUTATION_RECORDED: &str = "civ.society.reputation-event-recorded";
const EVENT_ROLE_ASSIGNED: &str = "civ.society.role-assigned";
const EVENT_COLLABORATION_RECORDED: &str = "civ.society.collaboration-recorded";

#[derive(Error, Debug)]
pub enum SocietyError {
    #[error("EventStore error: {0}")]
    EventStore(#[from] EventStoreError),
    #[error("Anti-self-endorsement violation: bot cannot self-award positive reputation")]
    SelfEndorsementBlocked,
    #[error("Invalid parameter: {0}")]
    InvalidParameter(&'static str),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
}

pub struct SocietyManager {
    store: CivEventStore,
}

impl SocietyManager {
    pub fn new(store: CivEventStore) -> Self {
        Self { store }
    }

    pub fn establish_relationship(
        &self,
        from_bot: &str,
        to_bot: &str,
        relation_type: &str,
        weight: f64,
        evidence_refs: Vec<String>,
    ) -> Result<RelationshipEdge, SocietyError> {
        if from_bot.is_empty() || to_bot.is_empty() {
            return Err(SocietyError::InvalidParameter("bot id cannot be empty"));
        }

        let edge = RelationshipEdge {
            id: generate_id("rel"),
            from_bot: from_bot.to_string(),
            to_bot: to_bot.to_string(),
            relation_type: relation_type.to_string(),
            weight,
            valid_from: now_timestamp(),
            valid_to: None,
            evidence_refs,
            schema_version: 1,
            metadata: serde_json::Value::Object(serde_json::Map::new()),
        };

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_RELATION_ESTABLISHED.to_string(),
            trace_id: None,
            correlation_id: None,
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now_timestamp(),
            payload: serde_json::json!({ "edge": edge }),
        };
        self.store.append(&ev)?;

        Ok(edge)
    }

    pub fn record_reputation(
        &self,
        subject_bot: &str,
        domain: &str,
        evidence_ref: &str,
        outcome: &str,
        delta_hint: f64,
        actor_bot: Option<&str>,
    ) -> Result<ReputationEvent, SocietyError> {
        if subject_bot.is_empty() || domain.is_empty() {
            return Err(SocietyError::InvalidParameter("subject and domain required"));
        }

        // Anti-self-endorsement invariant
        if let Some(actor) = actor_bot {
            if actor == subject_bot && outcome == "success" && delta_hint > 0.0 {
                return Err(SocietyError::SelfEndorsementBlocked);
            }
        }

        let event = ReputationEvent {
            id: generate_id("rep"),
            subject_bot: subject_bot.to_string(),
            domain: domain.to_string(),
            evidence_ref: evidence_ref.to_string(),
            outcome: outcome.to_string(),
            delta_hint,
            actor_bot: actor_bot.map(|s| s.to_string()),
            occurred_at: now_timestamp(),
            schema_version: 1,
            metadata: serde_json::Value::Object(serde_json::Map::new()),
        };

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_REPUTATION_RECORDED.to_string(),
            trace_id: None,
            correlation_id: None,
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now_timestamp(),
            payload: serde_json::json!({ "reputation": event }),
        };
        self.store.append(&ev)?;

        Ok(event)
    }

    pub fn get_relationships(&self, bot_id: &str) -> Result<Vec<RelationshipEdge>, SocietyError> {
        let events = self.store.get_all()?;
        let mut edges = Vec::new();

        for e in events {
            if e.name == EVENT_RELATION_ESTABLISHED {
                if let Some(edge_val) = e.payload.get("edge") {
                    let edge: RelationshipEdge = serde_json::from_value(edge_val.clone())?;
                    if edge.from_bot == bot_id || edge.to_bot == bot_id {
                        edges.push(edge);
                    }
                }
            }
        }

        Ok(edges)
    }

    pub fn get_reputation_vector(&self, bot_id: &str) -> Result<ReputationVector, SocietyError> {
        let events = self.store.get_all()?;
        let mut domain_events: HashMap<String, Vec<ReputationEvent>> = HashMap::new();

        for e in events {
            if e.name == EVENT_REPUTATION_RECORDED {
                if let Some(rep_val) = e.payload.get("reputation") {
                    let rep: ReputationEvent = serde_json::from_value(rep_val.clone())?;
                    if rep.subject_bot == bot_id {
                        domain_events.entry(rep.domain.clone()).or_default().push(rep);
                    }
                }
            }
        }

        let mut domain_scores = HashMap::new();
        let mut total_score = 0.0;
        let mut total_evidence = 0;
        let now = now_timestamp();

        for (domain, d_events) in domain_events {
            let mut score = 0.5; // neutral baseline
            let count = d_events.len() as u32;
            let mut last_updated = 0.0;

            for ev in d_events {
                let delta = match ev.outcome.as_str() {
                    "success" => ev.delta_hint.abs().max(0.1),
                    "failure" => -ev.delta_hint.abs().max(0.1),
                    _ => 0.0,
                };
                score = (score + delta).clamp(0.0, 1.0);
                if ev.occurred_at > last_updated {
                    last_updated = ev.occurred_at;
                }
            }

            let confidence = (count as f64 / 5.0).min(1.0);
            total_score += score;
            total_evidence += count;

            domain_scores.insert(
                domain.clone(),
                DomainScore {
                    domain,
                    score,
                    confidence,
                    evidence_count: count,
                    last_updated,
                },
            );
        }

        let overall = if !domain_scores.is_empty() {
            total_score / domain_scores.len() as f64
        } else {
            0.5
        };

        Ok(ReputationVector {
            bot_id: bot_id.to_string(),
            domains: domain_scores,
            overall_score: overall,
            evidence_count: total_evidence,
            as_of: now,
        })
    }

    pub fn select_specialists(
        &self,
        candidate_bots: &[String],
        domain: &str,
        count: usize,
    ) -> Result<Vec<(String, f64)>, SocietyError> {
        let mut ranked = Vec::new();

        for bot_id in candidate_bots {
            let vec = self.get_reputation_vector(bot_id)?;
            let score = vec
                .domains
                .get(domain)
                .map(|ds| ds.score * ds.confidence + 0.5 * (1.0 - ds.confidence))
                .unwrap_or(0.5); // cold start prior
            ranked.push((bot_id.clone(), score));
        }

        ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        ranked.truncate(count);
        Ok(ranked)
    }

    pub fn assign_role(
        &self,
        session_id: &str,
        bot_id: &str,
        role: &str,
        rationale: &str,
    ) -> Result<RoleAssignment, SocietyError> {
        let assignment = RoleAssignment {
            id: generate_id("role"),
            council_session_id: session_id.to_string(),
            bot_id: bot_id.to_string(),
            role: role.to_string(),
            rationale: rationale.to_string(),
            assigned_at: now_timestamp(),
            expires_at: None,
            metadata: serde_json::Value::Object(serde_json::Map::new()),
        };

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_ROLE_ASSIGNED.to_string(),
            trace_id: None,
            correlation_id: None,
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now_timestamp(),
            payload: serde_json::json!({ "role": assignment }),
        };
        self.store.append(&ev)?;

        Ok(assignment)
    }

    pub fn record_collaboration(
        &self,
        participants: Vec<String>,
        task_ref: &str,
        outcome_ref: &str,
        reviewer_refs: Vec<String>,
        score: Option<f64>,
    ) -> Result<CollaborationRecord, SocietyError> {
        let record = CollaborationRecord {
            id: generate_id("collab"),
            participants,
            task_ref: task_ref.to_string(),
            outcome_ref: outcome_ref.to_string(),
            reviewer_refs,
            recorded_at: now_timestamp(),
            score,
            metadata: serde_json::Value::Object(serde_json::Map::new()),
        };

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_COLLABORATION_RECORDED.to_string(),
            trace_id: None,
            correlation_id: None,
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now_timestamp(),
            payload: serde_json::json!({ "collaboration": record }),
        };
        self.store.append(&ev)?;

        Ok(record)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_anti_self_endorsement_blocked() {
        let store = CivEventStore::in_memory().unwrap();
        let mgr = SocietyManager::new(store);

        let res = mgr.record_reputation(
            "bot1",
            "security",
            "ev-1",
            "success",
            0.3,
            Some("bot1"), // self actor!
        );

        assert!(matches!(res, Err(SocietyError::SelfEndorsementBlocked)));
    }

    #[test]
    fn test_reputation_vector_and_specialist_selection() {
        let store = CivEventStore::in_memory().unwrap();
        let mgr = SocietyManager::new(store);

        // bot1 receives positive security review from bot2
        mgr.record_reputation(
            "bot1",
            "security",
            "ev-sec-1",
            "success",
            0.3,
            Some("bot2"),
        )
        .unwrap();

        // bot2 receives positive code_quality review from bot1
        mgr.record_reputation(
            "bot2",
            "code_quality",
            "ev-cq-1",
            "success",
            0.3,
            Some("bot1"),
        )
        .unwrap();

        let v1 = mgr.get_reputation_vector("bot1").unwrap();
        assert!(v1.domains.contains_key("security"));
        assert!(v1.domains["security"].score > 0.5);

        let candidates = vec!["bot1".to_string(), "bot2".to_string(), "bot3".to_string()];
        let selected_sec = mgr.select_specialists(&candidates, "security", 1).unwrap();
        assert_eq!(selected_sec[0].0, "bot1");

        let selected_cq = mgr.select_specialists(&candidates, "code_quality", 1).unwrap();
        assert_eq!(selected_cq[0].0, "bot2");
    }

    #[test]
    fn test_relationships_and_roles() {
        let store = CivEventStore::in_memory().unwrap();
        let mgr = SocietyManager::new(store);

        let edge = mgr
            .establish_relationship(
                "bot1",
                "bot2",
                "collaborates_with",
                0.9,
                vec!["session-42".to_string()],
            )
            .unwrap();

        assert_eq!(edge.relation_type, "collaborates_with");

        let rels = mgr.get_relationships("bot1").unwrap();
        assert_eq!(rels.len(), 1);
        assert_eq!(rels[0].to_bot, "bot2");

        let role = mgr
            .assign_role("sess-1", "bot1", "skeptic", "Rigorous security scrutiny")
            .unwrap();
        assert_eq!(role.role, "skeptic");
    }
}
