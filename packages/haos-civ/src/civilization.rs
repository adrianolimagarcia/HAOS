use serde::{Deserialize, Serialize};

use crate::crypto::{generate_id, now_timestamp};
use crate::event_store::CivEventStore;
use crate::models::CivEvent;

pub const EVENT_CONSTITUTION_ENACTED: &str = "civ.constitution.enacted";
pub const EVENT_POLICY_EVALUATED: &str = "civ.policy.evaluated";
pub const EVENT_ASSERTION_RECORDED: &str = "civ.memory.assertion_recorded";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ConstitutionRule {
    pub id: String,
    pub name: String,
    pub description: String,
    pub rule_type: String, // "hard_deny" | "advisory"
    pub target_action: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ConstitutionVersion {
    pub version: u32,
    pub title: String,
    pub rules: Vec<ConstitutionRule>,
    pub approved_by: String,
    pub effective_at: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PolicyDecision {
    pub id: String,
    pub rule_id: Option<String>,
    pub subject_bot: String,
    pub action: String,
    pub resource: String,
    pub result: String, // "allow" | "deny" | "require_approval"
    pub reason: String,
    pub evaluated_at: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct KnowledgeAssertion {
    pub id: String,
    pub subject: String,
    pub predicate: String,
    pub object: String,
    pub confidence: f64,
    pub provenance_ref: String,
    pub valid_from: f64,
    pub valid_to: Option<f64>,
}

pub struct CivilizationManager {
    store: CivEventStore,
}

impl CivilizationManager {
    pub fn new(store: CivEventStore) -> Self {
        Self { store }
    }

    pub fn enact_constitution(
        &self,
        version: u32,
        title: &str,
        rules: Vec<ConstitutionRule>,
        approved_by: &str,
    ) -> Result<ConstitutionVersion, crate::event_store::EventStoreError> {
        let cv = ConstitutionVersion {
            version,
            title: title.to_string(),
            rules,
            approved_by: approved_by.to_string(),
            effective_at: now_timestamp(),
        };

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_CONSTITUTION_ENACTED.to_string(),
            trace_id: None,
            correlation_id: None,
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: cv.effective_at,
            payload: serde_json::to_value(&cv).unwrap_or_default(),
        };
        self.store.append(&ev)?;
        Ok(cv)
    }

    pub fn get_active_constitution(&self) -> Result<Option<ConstitutionVersion>, crate::event_store::EventStoreError> {
        let events = self.store.get_all()?;
        let mut active: Option<ConstitutionVersion> = None;

        for e in events {
            if e.name == EVENT_CONSTITUTION_ENACTED {
                if let Ok(cv) = serde_json::from_value::<ConstitutionVersion>(e.payload) {
                    if active.as_ref().map_or(true, |cur| cv.version > cur.version) {
                        active = Some(cv);
                    }
                }
            }
        }
        Ok(active)
    }

    pub fn evaluate_policy(
        &self,
        subject_bot: &str,
        action: &str,
        resource: &str,
    ) -> Result<PolicyDecision, crate::event_store::EventStoreError> {
        let active = self.get_active_constitution()?;
        let mut result = "allow".to_string();
        let mut matched_rule: Option<String> = None;
        let mut reason = "No restrictive rule matched".to_string();

        if let Some(const_ver) = active {
            for rule in &const_ver.rules {
                if rule.target_action == "*" || rule.target_action == action {
                    if rule.rule_type == "hard_deny" {
                        result = "deny".to_string();
                        matched_rule = Some(rule.id.clone());
                        reason = format!("Hard deny by rule '{}': {}", rule.name, rule.description);
                        break;
                    } else if rule.rule_type == "advisory" && result == "allow" {
                        result = "require_approval".to_string();
                        matched_rule = Some(rule.id.clone());
                        reason = format!("Advisory warning by rule '{}': {}", rule.name, rule.description);
                    }
                }
            }
        }

        let dec = PolicyDecision {
            id: generate_id("pol"),
            rule_id: matched_rule,
            subject_bot: subject_bot.to_string(),
            action: action.to_string(),
            resource: resource.to_string(),
            result,
            reason,
            evaluated_at: now_timestamp(),
        };

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_POLICY_EVALUATED.to_string(),
            trace_id: None,
            correlation_id: None,
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: dec.evaluated_at,
            payload: serde_json::to_value(&dec).unwrap_or_default(),
        };
        self.store.append(&ev)?;
        Ok(dec)
    }

    pub fn record_assertion(
        &self,
        subject: &str,
        predicate: &str,
        object: &str,
        confidence: f64,
        provenance_ref: &str,
    ) -> Result<KnowledgeAssertion, crate::event_store::EventStoreError> {
        let assertion = KnowledgeAssertion {
            id: generate_id("assert"),
            subject: subject.to_string(),
            predicate: predicate.to_string(),
            object: object.to_string(),
            confidence,
            provenance_ref: provenance_ref.to_string(),
            valid_from: now_timestamp(),
            valid_to: None,
        };

        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_ASSERTION_RECORDED.to_string(),
            trace_id: None,
            correlation_id: None,
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: assertion.valid_from,
            payload: serde_json::to_value(&assertion).unwrap_or_default(),
        };
        self.store.append(&ev)?;
        Ok(assertion)
    }

    pub fn query_knowledge(&self, subject: Option<&str>, predicate: Option<&str>) -> Result<Vec<KnowledgeAssertion>, crate::event_store::EventStoreError> {
        let events = self.store.get_all()?;
        let mut results = Vec::new();

        for e in events {
            if e.name == EVENT_ASSERTION_RECORDED {
                if let Ok(a) = serde_json::from_value::<KnowledgeAssertion>(e.payload) {
                    if let Some(sub) = subject {
                        if a.subject != sub {
                            continue;
                        }
                    }
                    if let Some(pred) = predicate {
                        if a.predicate != pred {
                            continue;
                        }
                    }
                    results.push(a);
                }
            }
        }
        Ok(results)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_constitution_policy_and_memory() {
        let store = CivEventStore::in_memory().unwrap();
        let civ = CivilizationManager::new(store);

        let rules = vec![
            ConstitutionRule {
                id: "rule-protect-soul".to_string(),
                name: "Protect Bot Soul".to_string(),
                description: "Cannot mutate persistent soul without evolution proposal".to_string(),
                rule_type: "hard_deny".to_string(),
                target_action: "mutate_soul_direct".to_string(),
            },
            ConstitutionRule {
                id: "rule-warn-expensive".to_string(),
                name: "Warn On Heavy Model".to_string(),
                description: "Requires approval for model with context > 100k".to_string(),
                rule_type: "advisory".to_string(),
                target_action: "invoke_heavy_model".to_string(),
            },
        ];

        let const_v1 = civ
            .enact_constitution(1, "Civilization Constitution V1", rules, "council-founder")
            .unwrap();
        assert_eq!(const_v1.version, 1);

        let active = civ.get_active_constitution().unwrap().unwrap();
        assert_eq!(active.rules.len(), 2);

        // Policy evaluation: hard deny
        let dec_deny = civ
            .evaluate_policy("bot-rogue", "mutate_soul_direct", "bots/coder/SOUL.md")
            .unwrap();
        assert_eq!(dec_deny.result, "deny");
        assert_eq!(dec_deny.rule_id.as_deref(), Some("rule-protect-soul"));

        // Policy evaluation: advisory -> require_approval
        let dec_adv = civ
            .evaluate_policy("bot-worker", "invoke_heavy_model", "gpt-4-32k")
            .unwrap();
        assert_eq!(dec_adv.result, "require_approval");

        // Policy evaluation: unconstrained -> allow
        let dec_allow = civ
            .evaluate_policy("bot-worker", "read_file", "README.md")
            .unwrap();
        assert_eq!(dec_allow.result, "allow");

        // Civilization Memory assertions
        let assert1 = civ
            .record_assertion(
                "bot-coder",
                "implements",
                "haos-civ-rust",
                0.99,
                "commit:d689488",
            )
            .unwrap();
        assert_eq!(assert1.subject, "bot-coder");

        let queried = civ.query_knowledge(Some("bot-coder"), None).unwrap();
        assert_eq!(queried.len(), 1);
        assert_eq!(queried[0].predicate, "implements");
    }
}
