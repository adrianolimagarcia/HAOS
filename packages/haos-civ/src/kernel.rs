use serde::{Deserialize, Serialize};
use std::time::{SystemTime, UNIX_EPOCH};
use thiserror::Error;

#[derive(Error, Debug)]
pub enum KernelError {
    #[error("Invalid state transition from {from:?} to {to:?}: {reason}")]
    InvalidStateTransition {
        from: AgentState,
        to: AgentState,
        reason: String,
    },
    #[error("Contract violation: {0}")]
    ContractViolation(String),
    #[error("Memory validation error: {0}")]
    MemoryValidation(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentRole {
    Planner,
    Builder,
    Critic,
    Validator,
    Promoter,
    Security,
    Analyst,
    Operator,
    General,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ToolRiskTier {
    Read = 1,
    Low = 2,
    Medium = 3,
    High = 4,
    Critical = 5,
}

impl ToolRiskTier {
    pub fn allows(&self, other: ToolRiskTier) -> bool {
        (*self as u8) >= (other as u8)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryScope {
    Working,
    Session,
    Project,
    Domain,
    Global,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentState {
    Created,
    Planning,
    Ready,
    Executing,
    Validating,
    Reflecting,
    Completed,
    Failed,
    Blocked,
    Cancelled,
    RolledBack,
}

impl AgentState {
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            AgentState::Completed
                | AgentState::Failed
                | AgentState::Cancelled
                | AgentState::RolledBack
        )
    }

    pub fn can_transition_to(&self, target: AgentState) -> bool {
        match self {
            AgentState::Created => matches!(
                target,
                AgentState::Planning | AgentState::Ready | AgentState::Failed | AgentState::Cancelled
            ),
            AgentState::Planning => matches!(
                target,
                AgentState::Ready | AgentState::Blocked | AgentState::Failed | AgentState::Cancelled
            ),
            AgentState::Ready => matches!(
                target,
                AgentState::Executing | AgentState::Blocked | AgentState::Failed | AgentState::Cancelled
            ),
            AgentState::Executing => matches!(
                target,
                AgentState::Validating
                    | AgentState::Blocked
                    | AgentState::Failed
                    | AgentState::Cancelled
                    | AgentState::RolledBack
            ),
            AgentState::Validating => matches!(
                target,
                AgentState::Reflecting
                    | AgentState::Executing
                    | AgentState::Failed
                    | AgentState::RolledBack
                    | AgentState::Blocked
                    | AgentState::Cancelled
            ),
            AgentState::Reflecting => matches!(
                target,
                AgentState::Completed
                    | AgentState::Planning
                    | AgentState::Failed
                    | AgentState::RolledBack
            ),
            AgentState::Blocked => matches!(
                target,
                AgentState::Planning
                    | AgentState::Ready
                    | AgentState::Executing
                    | AgentState::Cancelled
                    | AgentState::Failed
            ),
            AgentState::Completed
            | AgentState::Failed
            | AgentState::Cancelled
            | AgentState::RolledBack => false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateTransitionRecord {
    pub from_state: AgentState,
    pub to_state: AgentState,
    pub timestamp: f64,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentStateMachine {
    pub agent_id: String,
    pub current_state: AgentState,
    pub history: Vec<StateTransitionRecord>,
}

impl AgentStateMachine {
    pub fn new(agent_id: impl Into<String>) -> Self {
        let now = now_ts();
        Self {
            agent_id: agent_id.into(),
            current_state: AgentState::Created,
            history: vec![StateTransitionRecord {
                from_state: AgentState::Created,
                to_state: AgentState::Created,
                timestamp: now,
                reason: "Instantiated".to_string(),
            }],
        }
    }

    pub fn transition_to(&mut self, target: AgentState, reason: &str) -> Result<(), KernelError> {
        if !self.current_state.can_transition_to(target) {
            return Err(KernelError::InvalidStateTransition {
                from: self.current_state,
                to: target,
                reason: reason.to_string(),
            });
        }

        let from = self.current_state;
        self.current_state = target;
        self.history.push(StateTransitionRecord {
            from_state: from,
            to_state: target,
            timestamp: now_ts(),
            reason: reason.to_string(),
        });

        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConfidenceVector {
    pub correctness: f64,
    pub test_coverage: f64,
    pub executions: u64,
    pub failures: u64,
    pub rollbacks: u64,
    pub last_executed_at: f64,
    pub half_life_days: f64,
}

impl Default for ConfidenceVector {
    fn default() -> Self {
        Self {
            correctness: 1.0,
            test_coverage: 0.8,
            executions: 1,
            failures: 0,
            rollbacks: 0,
            last_executed_at: now_ts(),
            half_life_days: 30.0,
        }
    }
}

impl ConfidenceVector {
    pub fn rollback_rate(&self) -> f64 {
        if self.executions == 0 {
            0.0
        } else {
            (self.rollbacks as f64) / (self.executions as f64)
        }
    }

    pub fn age_decay(&self) -> f64 {
        let elapsed_secs = (now_ts() - self.last_executed_at).max(0.0);
        let elapsed_days = elapsed_secs / 86400.0;
        if self.half_life_days <= 0.0 {
            1.0
        } else {
            0.5_f64.powf(elapsed_days / self.half_life_days)
        }
    }

    pub fn composite_score(&self) -> f64 {
        if self.executions == 0 {
            return 0.0;
        }

        let volume_factor = (((self.executions as f64) + 1.0).log10() / 2.0).min(1.0);
        let base_quality = 0.40 * self.correctness + 0.30 * self.test_coverage + 0.30 * volume_factor;
        let penalized = base_quality * (1.0 - 0.90 * self.rollback_rate());
        let final_score = penalized * self.age_decay();
        final_score.clamp(0.0, 1.0)
    }

    pub fn record_outcome(&mut self, success: bool, test_passed: bool, rolled_back: bool) {
        self.executions += 1;
        self.last_executed_at = now_ts();

        if !success {
            self.failures += 1;
        }
        if rolled_back {
            self.rollbacks += 1;
        }

        let alpha = 1.0 / (self.executions.min(50) as f64);
        let curr_corr = if success && !rolled_back { 1.0 } else { 0.0 };
        self.correctness = (1.0 - alpha) * self.correctness + alpha * curr_corr;

        let curr_cov = if test_passed { 1.0 } else { 0.0 };
        self.test_coverage = (1.0 - alpha) * self.test_coverage + alpha * curr_cov;
    }
}

fn now_ts() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fsm_happy_path() {
        let mut fsm = AgentStateMachine::new("test-agent");
        assert_eq!(fsm.current_state, AgentState::Created);

        assert!(fsm.transition_to(AgentState::Planning, "start plan").is_ok());
        assert_eq!(fsm.current_state, AgentState::Planning);

        assert!(fsm.transition_to(AgentState::Ready, "ready for exec").is_ok());
        assert_eq!(fsm.current_state, AgentState::Ready);

        assert!(fsm.transition_to(AgentState::Executing, "running").is_ok());
        assert_eq!(fsm.current_state, AgentState::Executing);

        assert!(fsm.transition_to(AgentState::Validating, "tests").is_ok());
        assert_eq!(fsm.current_state, AgentState::Validating);

        assert!(fsm.transition_to(AgentState::Reflecting, "lessons").is_ok());
        assert_eq!(fsm.current_state, AgentState::Reflecting);

        assert!(fsm.transition_to(AgentState::Completed, "done").is_ok());
        assert_eq!(fsm.current_state, AgentState::Completed);
        assert!(fsm.current_state.is_terminal());

        // Cannot transition from terminal Completed
        assert!(fsm.transition_to(AgentState::Planning, "reopen").is_err());
    }

    #[test]
    fn test_fsm_invalid_direct_jump() {
        let mut fsm = AgentStateMachine::new("test-agent-jump");
        // Direct jump from Created to Executing is disallowed
        let res = fsm.transition_to(AgentState::Executing, "skip");
        assert!(res.is_err());
    }

    #[test]
    fn test_confidence_vector_calculation() {
        let mut cv = ConfidenceVector::default();
        let initial_score = cv.composite_score();
        assert!(initial_score > 0.3);

        // Record a failure with rollback
        cv.record_outcome(false, false, true);
        let penal_score = cv.composite_score();
        assert!(penal_score < initial_score);

        // Record 10 successes
        for _ in 0..10 {
            cv.record_outcome(true, true, false);
        }
        let recovered_score = cv.composite_score();
        assert!(recovered_score > penal_score);
    }

    #[test]
    fn test_tool_risk_tier_comparison() {
        assert!(ToolRiskTier::High.allows(ToolRiskTier::Medium));
        assert!(ToolRiskTier::Critical.allows(ToolRiskTier::High));
        assert!(!ToolRiskTier::Low.allows(ToolRiskTier::High));
        assert!(ToolRiskTier::Medium.allows(ToolRiskTier::Medium));
    }
}
