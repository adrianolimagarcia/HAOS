use std::collections::HashMap;
use thiserror::Error;

use crate::crypto::{generate_id, now_timestamp};
use crate::event_store::{CivEventStore, EventStoreError};
use crate::models::{CivEvent, CouncilBudget, CouncilSession, CouncilSpec, DebateTurn, DecisionRecord};

pub const EVENT_COUNCIL_CREATED: &str = "civ.council.created";
pub const EVENT_SESSION_STARTED: &str = "civ.council.session-started";
pub const EVENT_POSITION_SUBMITTED: &str = "civ.council.position-submitted";
pub const EVENT_PHASE_CHANGED: &str = "civ.council.phase-changed";
pub const EVENT_DEBATE_ROUND_STARTED: &str = "civ.council.debate-round-started";
pub const EVENT_DEBATE_TURN_RECORDED: &str = "civ.council.debate-turn-recorded";
pub const EVENT_DECISION_RECORDED: &str = "civ.council.decision-recorded";
pub const EVENT_SESSION_FAILED: &str = "civ.council.session-failed";
pub const EVENT_SESSION_ABORTED: &str = "civ.council.session-aborted";

#[derive(Error, Debug)]
pub enum CouncilError {
    #[error("EventStore error: {0}")]
    EventStore(#[from] EventStoreError),
    #[error("Council already exists: {0}")]
    CouncilAlreadyExists(String),
    #[error("Council not found: {0}")]
    CouncilNotFound(String),
    #[error("CouncilSession not found: {0}")]
    SessionNotFound(String),
    #[error("Quorum failure: CouncilSession requires at least 2 members")]
    QuorumFailure,
    #[error("Unauthorized bot: {0} is not an enrolled member of this session")]
    UnauthorizedMember(String),
    #[error("No positions submitted: cannot record decision without deliberation positions")]
    NoPositions,
    #[error("Illegal transition: cannot transition from {0} to {1}")]
    IllegalTransition(String, String),
    #[error("Budget exhausted: {0}")]
    BudgetExhausted(String),
    #[error("JSON serialization error: {0}")]
    Json(#[from] serde_json::Error),
}

pub fn is_legal_transition(from_phase: &str, to_phase: &str) -> bool {
    match from_phase {
        "created" => matches!(to_phase, "selecting_members" | "independent_analysis" | "aborted" | "failed"),
        "selecting_members" => matches!(to_phase, "independent_analysis" | "aborted" | "failed"),
        "independent_analysis" => matches!(to_phase, "debate_round" | "synthesis" | "paused" | "aborted" | "failed"),
        "debate_round" => matches!(to_phase, "debate_round" | "synthesis" | "paused" | "aborted" | "failed"),
        "synthesis" => matches!(to_phase, "policy_check" | "decision_recorded" | "paused" | "aborted" | "failed"),
        "policy_check" => matches!(to_phase, "decision_recorded" | "action_pending" | "paused" | "aborted" | "failed"),
        "decision_recorded" => matches!(to_phase, "action_pending" | "completed" | "paused" | "aborted" | "failed"),
        "action_pending" => matches!(to_phase, "completed" | "failed" | "paused" | "aborted"),
        "paused" => matches!(to_phase, "independent_analysis" | "debate_round" | "synthesis" | "policy_check" | "action_pending" | "aborted" | "failed"),
        _ => false,
    }
}

pub struct CouncilManager {
    store: CivEventStore,
}

impl CouncilManager {
    pub fn new(store: CivEventStore) -> Self {
        Self { store }
    }

    fn materialize_state(
        &self,
    ) -> Result<
        (
            HashMap<String, CouncilSpec>,
            HashMap<String, CouncilSession>,
            HashMap<String, DecisionRecord>,
        ),
        CouncilError,
    > {
        let events = self.store.get_all()?;
        let mut councils: HashMap<String, CouncilSpec> = HashMap::new();
        let mut sessions: HashMap<String, CouncilSession> = HashMap::new();
        let mut decisions: HashMap<String, DecisionRecord> = HashMap::new();

        for e in events {
            match e.name.as_str() {
                EVENT_COUNCIL_CREATED => {
                    if let Some(spec_val) = e.payload.get("spec") {
                        let spec: CouncilSpec = serde_json::from_value(spec_val.clone())?;
                        councils.insert(spec.id.clone(), spec);
                    }
                }
                EVENT_SESSION_STARTED => {
                    if let Some(sess_val) = e.payload.get("session") {
                        let sess: CouncilSession = serde_json::from_value(sess_val.clone())?;
                        sessions.insert(sess.session_id.clone(), sess);
                    }
                }
                EVENT_POSITION_SUBMITTED => {
                    let sess_id = match e.payload.get("session_id").and_then(|v| v.as_str()) {
                        Some(id) => id,
                        None => continue,
                    };
                    let bot_id = match e.payload.get("bot_id").and_then(|v| v.as_str()) {
                        Some(id) => id,
                        None => continue,
                    };
                    if let Some(sess) = sessions.get_mut(sess_id) {
                        if let Some(pos) = e.payload.get("position") {
                            sess.positions.insert(bot_id.to_string(), pos.clone());
                        }
                        if let Some(dis) = e.payload.get("dissent").and_then(|v| v.as_str()) {
                            sess.dissent.insert(bot_id.to_string(), dis.to_string());
                        }
                        sess.revision += 1;
                        sess.updated_at = e.timestamp;
                    }
                }
                EVENT_PHASE_CHANGED => {
                    let sess_id = match e.payload.get("session_id").and_then(|v| v.as_str()) {
                        Some(id) => id,
                        None => continue,
                    };
                    let phase = match e.payload.get("phase").and_then(|v| v.as_str()) {
                        Some(p) => p,
                        None => continue,
                    };
                    if let Some(sess) = sessions.get_mut(sess_id) {
                        sess.phase = phase.to_string();
                        sess.revision += 1;
                        sess.updated_at = e.timestamp;
                    }
                }
                EVENT_DEBATE_TURN_RECORDED => {
                    let sess_id = match e.payload.get("session_id").and_then(|v| v.as_str()) {
                        Some(id) => id,
                        None => continue,
                    };
                    if let Some(turn_val) = e.payload.get("turn") {
                        if let Ok(turn) = serde_json::from_value::<DebateTurn>(turn_val.clone()) {
                            if let Some(sess) = sessions.get_mut(sess_id) {
                                sess.debate_turns.push(turn);
                                sess.revision += 1;
                                sess.updated_at = e.timestamp;
                            }
                        }
                    }
                }
                EVENT_DECISION_RECORDED => {
                    if let Some(dec_val) = e.payload.get("decision") {
                        let dec: DecisionRecord = serde_json::from_value(dec_val.clone())?;
                        if let Some(sess) = sessions.get_mut(&dec.council_session_id) {
                            sess.decision_id = Some(dec.id.clone());
                            sess.phase = "completed".to_string();
                            sess.revision += 1;
                            sess.updated_at = e.timestamp;
                        }
                        decisions.insert(dec.id.clone(), dec);
                    }
                }
                EVENT_SESSION_FAILED => {
                    let sess_id = match e.payload.get("session_id").and_then(|v| v.as_str()) {
                        Some(id) => id,
                        None => continue,
                    };
                    if let Some(sess) = sessions.get_mut(sess_id) {
                        sess.phase = "failed".to_string();
                        sess.error = e.payload.get("error").and_then(|v| v.as_str()).map(|s| s.to_string());
                        sess.revision += 1;
                        sess.updated_at = e.timestamp;
                    }
                }
                EVENT_SESSION_ABORTED => {
                    let sess_id = match e.payload.get("session_id").and_then(|v| v.as_str()) {
                        Some(id) => id,
                        None => continue,
                    };
                    if let Some(sess) = sessions.get_mut(sess_id) {
                        sess.phase = "aborted".to_string();
                        sess.error = e.payload.get("reason").and_then(|v| v.as_str()).map(|s| s.to_string());
                        sess.revision += 1;
                        sess.updated_at = e.timestamp;
                    }
                }
                _ => {}
            }
        }

        Ok((councils, sessions, decisions))
    }

    pub fn register(&self, spec: CouncilSpec) -> Result<CouncilSpec, CouncilError> {
        let (councils, _, _) = self.materialize_state()?;
        if councils.contains_key(&spec.id) {
            return Err(CouncilError::CouncilAlreadyExists(spec.id));
        }

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_COUNCIL_CREATED.to_string(),
            trace_id: None,
            correlation_id: None,
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now_timestamp(),
            payload: serde_json::json!({ "spec": spec }),
        };
        self.store.append(&ev)?;
        Ok(spec)
    }

    pub fn get(&self, council_id: &str) -> Result<Option<CouncilSpec>, CouncilError> {
        let (councils, _, _) = self.materialize_state()?;
        Ok(councils.get(council_id).cloned())
    }

    pub fn list(&self) -> Result<Vec<CouncilSpec>, CouncilError> {
        let (councils, _, _) = self.materialize_state()?;
        Ok(councils.into_values().collect())
    }

    pub fn start_session(
        &self,
        council_id: &str,
        objective: &str,
        members: Option<Vec<String>>,
        correlation_id: Option<String>,
    ) -> Result<CouncilSession, CouncilError> {
        self.start_session_with_budget(council_id, objective, members, correlation_id, None, None)
    }

    pub fn start_session_with_budget(
        &self,
        council_id: &str,
        objective: &str,
        members: Option<Vec<String>>,
        correlation_id: Option<String>,
        command_id: Option<String>,
        budget: Option<CouncilBudget>,
    ) -> Result<CouncilSession, CouncilError> {
        let (councils, sessions, _) = self.materialize_state()?;

        // Command deduplication
        if let Some(ref cmd_id) = command_id {
            for sess in sessions.values() {
                if sess.command_id.as_deref() == Some(cmd_id) {
                    return Ok(sess.clone());
                }
            }
        }

        let council = councils
            .get(council_id)
            .ok_or_else(|| CouncilError::CouncilNotFound(council_id.to_string()))?;

        let sess_members = members.unwrap_or_else(|| council.members.clone());
        if sess_members.len() < 2 {
            return Err(CouncilError::QuorumFailure);
        }

        let session_id = format!("csess-{council_id}-{}", &generate_id("s")[2..10]);
        let now = now_timestamp();

        let session = CouncilSession {
            session_id: session_id.clone(),
            council_id: council_id.to_string(),
            objective: objective.to_string(),
            members: sess_members,
            phase: "independent_analysis".to_string(),
            positions: HashMap::new(),
            dissent: HashMap::new(),
            leaf_runs: Vec::new(),
            decision_id: None,
            command_id,
            budget,
            debate_turns: Vec::new(),
            revision: 0,
            error: None,
            created_at: now,
            updated_at: now,
            correlation_id: correlation_id.clone(),
            metadata: serde_json::Value::Object(serde_json::Map::new()),
        };

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_SESSION_STARTED.to_string(),
            trace_id: None,
            correlation_id,
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now,
            payload: serde_json::json!({ "session": session }),
        };
        self.store.append(&ev)?;

        Ok(session)
    }

    pub fn advance_phase(
        &self,
        session_id: &str,
        phase: &str,
        correlation_id: Option<String>,
    ) -> Result<CouncilSession, CouncilError> {
        let (_, sessions, _) = self.materialize_state()?;
        let session = sessions
            .get(session_id)
            .ok_or_else(|| CouncilError::SessionNotFound(session_id.to_string()))?;

        if !is_legal_transition(&session.phase, phase) {
            return Err(CouncilError::IllegalTransition(
                session.phase.clone(),
                phase.to_string(),
            ));
        }

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_PHASE_CHANGED.to_string(),
            trace_id: None,
            correlation_id: correlation_id.or_else(|| session.correlation_id.clone()),
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now_timestamp(),
            payload: serde_json::json!({
                "session_id": session_id,
                "phase": phase,
                "previous_phase": session.phase
            }),
        };
        self.store.append(&ev)?;

        let (_, updated_sessions, _) = self.materialize_state()?;
        Ok(updated_sessions[session_id].clone())
    }

    pub fn record_debate_turn(
        &self,
        session_id: &str,
        turn: DebateTurn,
        correlation_id: Option<String>,
    ) -> Result<CouncilSession, CouncilError> {
        let (_, sessions, _) = self.materialize_state()?;
        let session = sessions
            .get(session_id)
            .ok_or_else(|| CouncilError::SessionNotFound(session_id.to_string()))?;

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_DEBATE_TURN_RECORDED.to_string(),
            trace_id: None,
            correlation_id: correlation_id.or_else(|| session.correlation_id.clone()),
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now_timestamp(),
            payload: serde_json::json!({
                "session_id": session_id,
                "turn": turn
            }),
        };
        self.store.append(&ev)?;

        let (_, updated_sessions, _) = self.materialize_state()?;
        Ok(updated_sessions[session_id].clone())
    }

    pub fn fail_session(
        &self,
        session_id: &str,
        error: &str,
        correlation_id: Option<String>,
    ) -> Result<CouncilSession, CouncilError> {
        let (_, sessions, _) = self.materialize_state()?;
        let session = sessions
            .get(session_id)
            .ok_or_else(|| CouncilError::SessionNotFound(session_id.to_string()))?;

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_SESSION_FAILED.to_string(),
            trace_id: None,
            correlation_id: correlation_id.or_else(|| session.correlation_id.clone()),
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now_timestamp(),
            payload: serde_json::json!({
                "session_id": session_id,
                "error": error
            }),
        };
        self.store.append(&ev)?;

        let (_, updated_sessions, _) = self.materialize_state()?;
        Ok(updated_sessions[session_id].clone())
    }

    pub fn submit_position(
        &self,
        session_id: &str,
        bot_id: &str,
        position: serde_json::Value,
        dissent: Option<String>,
    ) -> Result<CouncilSession, CouncilError> {
        let (_, sessions, _) = self.materialize_state()?;
        let session = sessions
            .get(session_id)
            .ok_or_else(|| CouncilError::SessionNotFound(session_id.to_string()))?;

        if !session.members.contains(&bot_id.to_string()) {
            return Err(CouncilError::UnauthorizedMember(bot_id.to_string()));
        }

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_POSITION_SUBMITTED.to_string(),
            trace_id: None,
            correlation_id: session.correlation_id.clone(),
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now_timestamp(),
            payload: serde_json::json!({
                "session_id": session_id,
                "bot_id": bot_id,
                "position": position,
                "dissent": dissent
            }),
        };
        self.store.append(&ev)?;

        let (_, updated_sessions, _) = self.materialize_state()?;
        Ok(updated_sessions[session_id].clone())
    }

    pub fn record_decision(
        &self,
        session_id: &str,
        synthesis: &str,
        decision: &str,
        confidence: f64,
        action_refs: Vec<String>,
    ) -> Result<DecisionRecord, CouncilError> {
        let (_, sessions, _) = self.materialize_state()?;
        let session = sessions
            .get(session_id)
            .ok_or_else(|| CouncilError::SessionNotFound(session_id.to_string()))?;

        if session.positions.is_empty() {
            return Err(CouncilError::NoPositions);
        }

        let dec_id = format!("dec-{}-{}", session.council_id, &generate_id("d")[2..10]);
        let now = now_timestamp();

        let record = DecisionRecord {
            id: dec_id,
            council_id: session.council_id.clone(),
            council_session_id: session_id.to_string(),
            objective: session.objective.clone(),
            participants: session.positions.keys().cloned().collect(),
            identity_version_refs: HashMap::new(),
            leaf_refs: session.leaf_runs.clone(),
            evidence_refs: Vec::new(),
            positions: session.positions.clone(),
            synthesis: synthesis.to_string(),
            dissent: session.dissent.clone(),
            decision: decision.to_string(),
            confidence,
            action_refs,
            policy_verified: true,
            created_at: now,
            correlation_id: session.correlation_id.clone(),
            schema_version: 1,
            metadata: serde_json::Value::Object(serde_json::Map::new()),
        };

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_DECISION_RECORDED.to_string(),
            trace_id: None,
            correlation_id: session.correlation_id.clone(),
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now,
            payload: serde_json::json!({ "decision": record }),
        };
        self.store.append(&ev)?;

        Ok(record)
    }

    pub fn get_session(&self, session_id: &str) -> Result<Option<CouncilSession>, CouncilError> {
        let (_, sessions, _) = self.materialize_state()?;
        Ok(sessions.get(session_id).cloned())
    }

    pub fn list_sessions(&self, council_id: Option<&str>) -> Result<Vec<CouncilSession>, CouncilError> {
        let (_, sessions, _) = self.materialize_state()?;
        let list: Vec<CouncilSession> = sessions
            .into_values()
            .filter(|s| match council_id {
                Some(cid) => s.council_id == cid,
                None => true,
            })
            .collect();
        Ok(list)
    }

    pub fn list_decisions(&self, council_id: Option<&str>) -> Result<Vec<DecisionRecord>, CouncilError> {
        let (_, _, decisions) = self.materialize_state()?;
        let list: Vec<DecisionRecord> = decisions
            .into_values()
            .filter(|d| match council_id {
                Some(cid) => d.council_id == cid,
                None => true,
            })
            .collect();
        Ok(list)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_council_flow_with_dissent() {
        let store = CivEventStore::in_memory().unwrap();
        let mgr = CouncilManager::new(store.clone());

        let spec = CouncilSpec {
            id: "arch".to_string(),
            purpose: "Architecture".to_string(),
            members: vec!["bot1".to_string(), "bot2".to_string()],
            roles: HashMap::new(),
            decision_mode: "consensus_with_dissent".to_string(),
            budget: serde_json::Value::Null,
            policy_ref: None,
            rules: Vec::new(),
            version: 1,
            schema_version: 1,
            metadata: serde_json::Value::Null,
        };
        mgr.register(spec).unwrap();

        let sess = mgr.start_session("arch", "Storage design", None, None).unwrap();
        assert_eq!(sess.phase, "independent_analysis");

        mgr.submit_position(
            &sess.session_id,
            "bot1",
            serde_json::json!("Prefer Rust engine"),
            None,
        )
        .unwrap();

        mgr.submit_position(
            &sess.session_id,
            "bot2",
            serde_json::json!("Prefer Hybrid engine"),
            Some("Python is better for prompt manipulation".to_string()),
        )
        .unwrap();

        let dec = mgr
            .record_decision(
                &sess.session_id,
                "Adopt Rust core with Python AI client",
                "approved",
                0.98,
                vec!["task:rust_core".to_string()],
            )
            .unwrap();

        assert_eq!(dec.decision, "approved");
        assert_eq!(dec.dissent.get("bot2").unwrap(), "Python is better for prompt manipulation");

        // Verify state rebuilt from fresh manager
        let mgr2 = CouncilManager::new(store);
        let sess2 = mgr2.get_session(&sess.session_id).unwrap().unwrap();
        assert_eq!(sess2.phase, "completed");
        assert_eq!(sess2.decision_id.unwrap(), dec.id);
    }

    #[test]
    fn test_council_fsm_and_budget() {
        let store = CivEventStore::in_memory().unwrap();
        let mgr = CouncilManager::new(store.clone());

        let spec = CouncilSpec {
            id: "infra".to_string(),
            purpose: "Infra Governance".to_string(),
            members: vec!["infra-1".to_string(), "infra-2".to_string()],
            roles: HashMap::new(),
            decision_mode: "majority".to_string(),
            budget: serde_json::Value::Null,
            policy_ref: None,
            rules: Vec::new(),
            version: 1,
            schema_version: 1,
            metadata: serde_json::Value::Null,
        };
        mgr.register(spec).unwrap();

        let mut budget = CouncilBudget::default();
        budget.max_rounds = 2;
        budget.reserve_round().unwrap();

        let sess = mgr
            .start_session_with_budget(
                "infra",
                "Scale cluster",
                None,
                None,
                Some("cmd-scale-1".to_string()),
                Some(budget),
            )
            .unwrap();

        // Legal transition
        let sess_advanced = mgr.advance_phase(&sess.session_id, "debate_round", None).unwrap();
        assert_eq!(sess_advanced.phase, "debate_round");

        // Record a debate turn
        let turn = DebateTurn {
            bot_id: "infra-1".to_string(),
            round_number: 1,
            phase: "debate_round".to_string(),
            position: "Provision 3 replicas".to_string(),
            critique_of: None,
            evidence_refs: vec!["metric:cpu_spike".to_string()],
            timestamp: now_timestamp(),
        };
        let sess_with_turn = mgr.record_debate_turn(&sess.session_id, turn, None).unwrap();
        assert_eq!(sess_with_turn.debate_turns.len(), 1);

        // Illegal transition fails
        let err = mgr.advance_phase(&sess.session_id, "completed", None).unwrap_err();
        match err {
            CouncilError::IllegalTransition(from, to) => {
                assert_eq!(from, "debate_round");
                assert_eq!(to, "completed");
            }
            _ => panic!("Expected IllegalTransition error"),
        }

        // Deduplication on identical command_id returns existing session
        let dup = mgr
            .start_session_with_budget(
                "infra",
                "Scale cluster",
                None,
                None,
                Some("cmd-scale-1".to_string()),
                None,
            )
            .unwrap();
        assert_eq!(dup.session_id, sess.session_id);
    }
}
