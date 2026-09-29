use std::collections::HashMap;
use thiserror::Error;

use crate::crypto::{generate_id, now_timestamp};
use crate::event_store::{CivEventStore, EventStoreError};
use crate::models::{BotIdentityBundle, CivEvent, IdentityVersion};

const EVENT_VERSION_CREATED: &str = "civ.bot.identity-version-created";
const EVENT_VERSION_ACTIVATED: &str = "civ.bot.identity-version-activated";
const EVENT_DRIFT_DETECTED: &str = "civ.bot.identity-drift-detected";

#[derive(Error, Debug)]
pub enum IdentityManagerError {
    #[error("EventStore error: {0}")]
    EventStore(#[from] EventStoreError),
    #[error("Version not found: {0}")]
    VersionNotFound(String),
    #[error("JSON serialization error: {0}")]
    Json(#[from] serde_json::Error),
}

#[derive(Default, Debug)]
struct BotState {
    versions: HashMap<String, IdentityVersion>,
    active_id: Option<String>,
}

pub struct IdentityManager {
    store: CivEventStore,
}

impl IdentityManager {
    pub fn new(store: CivEventStore) -> Self {
        Self { store }
    }

    fn materialize_state(&self) -> Result<HashMap<String, BotState>, IdentityManagerError> {
        let events = self.store.get_all()?;
        let mut state: HashMap<String, BotState> = HashMap::new();

        for e in events {
            match e.name.as_str() {
                EVENT_VERSION_CREATED => {
                    let bot_id = match e.payload.get("bot_id").and_then(|v| v.as_str()) {
                        Some(b) => b.to_string(),
                        None => continue,
                    };
                    let ver_val = match e.payload.get("version") {
                        Some(v) => v.clone(),
                        None => continue,
                    };
                    let ver: IdentityVersion = serde_json::from_value(ver_val)?;

                    let bot_state = state.entry(bot_id).or_default();
                    if bot_state.active_id.is_none() && ver.status == "active" {
                        bot_state.active_id = Some(ver.id.clone());
                    }
                    bot_state.versions.insert(ver.id.clone(), ver);
                }
                EVENT_VERSION_ACTIVATED => {
                    let bot_id = match e.payload.get("bot_id").and_then(|v| v.as_str()) {
                        Some(b) => b.to_string(),
                        None => continue,
                    };
                    let ver_id = match e.payload.get("version_id").and_then(|v| v.as_str()) {
                        Some(v) => v.to_string(),
                        None => continue,
                    };
                    if let Some(bot_state) = state.get_mut(&bot_id) {
                        if bot_state.versions.contains_key(&ver_id) {
                            bot_state.active_id = Some(ver_id);
                        }
                    }
                }
                _ => {}
            }
        }

        Ok(state)
    }

    pub fn create_version(
        &self,
        bot_id: &str,
        bundle: BotIdentityBundle,
        parent_id: Option<String>,
        activate: bool,
    ) -> Result<IdentityVersion, IdentityManagerError> {
        let state = self.materialize_state()?;
        let bot_state = state.get(bot_id);

        let next_version = match bot_state {
            Some(bs) => bs.versions.values().map(|v| v.version).max().unwrap_or(0) + 1,
            None => 1,
        };

        let version_id = format!("{bot_id}-v{next_version}-{}", &generate_id("iv")[3..11]);
        let parent = parent_id.or_else(|| bot_state.and_then(|bs| bs.active_id.clone()));

        let version = IdentityVersion {
            id: version_id.clone(),
            bot_id: bot_id.to_string(),
            version: next_version,
            bundle_hash: bundle.bundle_hash.clone(),
            parent_id: parent,
            bundle: Some(bundle),
            status: if activate { "active" } else { "draft" }.to_string(),
            created_at: now_timestamp(),
            schema_version: 1,
            metadata: serde_json::Value::Object(serde_json::Map::new()),
        };

        let ev_created = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_VERSION_CREATED.to_string(),
            trace_id: None,
            correlation_id: None,
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now_timestamp(),
            payload: serde_json::json!({
                "bot_id": bot_id,
                "version": version
            }),
        };
        self.store.append(&ev_created)?;

        if activate {
            self.activate_version(bot_id, &version_id)?;
        }

        Ok(version)
    }

    pub fn activate_version(
        &self,
        bot_id: &str,
        version_id: &str,
    ) -> Result<IdentityVersion, IdentityManagerError> {
        let state = self.materialize_state()?;
        let bot_state = state
            .get(bot_id)
            .ok_or_else(|| IdentityManagerError::VersionNotFound(version_id.to_string()))?;
        let version = bot_state
            .versions
            .get(version_id)
            .cloned()
            .ok_or_else(|| IdentityManagerError::VersionNotFound(version_id.to_string()))?;

        let ev_activated = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_VERSION_ACTIVATED.to_string(),
            trace_id: None,
            correlation_id: None,
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now_timestamp(),
            payload: serde_json::json!({
                "bot_id": bot_id,
                "version_id": version_id
            }),
        };
        self.store.append(&ev_activated)?;

        Ok(version)
    }

    pub fn get_active_version(
        &self,
        bot_id: &str,
    ) -> Result<Option<IdentityVersion>, IdentityManagerError> {
        let state = self.materialize_state()?;
        let bot_state = match state.get(bot_id) {
            Some(bs) => bs,
            None => return Ok(None),
        };
        match &bot_state.active_id {
            Some(aid) => Ok(bot_state.versions.get(aid).cloned()),
            None => Ok(None),
        }
    }

    pub fn get_version(
        &self,
        bot_id: &str,
        version_id: &str,
    ) -> Result<Option<IdentityVersion>, IdentityManagerError> {
        let state = self.materialize_state()?;
        Ok(state
            .get(bot_id)
            .and_then(|bs| bs.versions.get(version_id).cloned()))
    }

    pub fn list_versions(
        &self,
        bot_id: &str,
    ) -> Result<Vec<IdentityVersion>, IdentityManagerError> {
        let state = self.materialize_state()?;
        match state.get(bot_id) {
            Some(bs) => {
                let mut vers: Vec<IdentityVersion> = bs.versions.values().cloned().collect();
                vers.sort_by_key(|v| v.version);
                Ok(vers)
            }
            None => Ok(Vec::new()),
        }
    }

    pub fn rollback(
        &self,
        bot_id: &str,
        target_version_id: &str,
        reason: &str,
    ) -> Result<IdentityVersion, IdentityManagerError> {
        let target = self
            .get_version(bot_id, target_version_id)?
            .ok_or_else(|| IdentityManagerError::VersionNotFound(target_version_id.to_string()))?;

        let bundle = target
            .bundle
            .ok_or_else(|| IdentityManagerError::VersionNotFound("Missing bundle in target".into()))?;

        let mut restored_bundle = BotIdentityBundle::new(
            bot_id,
            bundle.identity_version,
            bundle.soul,
            bundle.identity,
            bundle.values,
        )
        .map_err(|e| IdentityManagerError::VersionNotFound(e.to_string()))?;

        restored_bundle.metadata = serde_json::json!({
            "rollback_from": target_version_id,
            "rollback_reason": reason
        });

        self.create_version(
            bot_id,
            restored_bundle,
            Some(target_version_id.to_string()),
            true,
        )
    }

    pub fn record_drift(
        &self,
        bot_id: &str,
        active_version_id: &str,
        detected_bundle_hash: &str,
    ) -> Result<(), IdentityManagerError> {
        let ev = CivEvent {
            event_id: generate_id("evt"),
            seq: 0,
            name: EVENT_DRIFT_DETECTED.to_string(),
            trace_id: None,
            correlation_id: None,
            causation_id: None,
            trust_level: "local_system".to_string(),
            schema_version: 1,
            timestamp: now_timestamp(),
            payload: serde_json::json!({
                "bot_id": bot_id,
                "active_version_id": active_version_id,
                "detected_bundle_hash": detected_bundle_hash
            }),
        };
        self.store.append(&ev)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_create_and_rollback_versions() {
        let store = CivEventStore::in_memory().unwrap();
        let mgr = IdentityManager::new(store.clone());

        let b1 = BotIdentityBundle::new("bot1", 1, "Soul 1", "Id 1", "Val 1").unwrap();
        let v1 = mgr.create_version("bot1", b1, None, true).unwrap();
        assert_eq!(v1.version, 1);

        let b2 = BotIdentityBundle::new("bot1", 2, "Soul 2 Drifting", "Id 1", "Val 1").unwrap();
        let v2 = mgr.create_version("bot1", b2, None, true).unwrap();
        assert_eq!(v2.version, 2);

        let active = mgr.get_active_version("bot1").unwrap().unwrap();
        assert_eq!(active.id, v2.id);

        // Rollback to v1 creates compensatory v3
        let v3 = mgr.rollback("bot1", &v1.id, "revert drift").unwrap();
        assert_eq!(v3.version, 3);
        assert_eq!(v3.bundle.unwrap().soul, "Soul 1");

        // Replay and projection from fresh IdentityManager instance
        let mgr2 = IdentityManager::new(store);
        let active2 = mgr2.get_active_version("bot1").unwrap().unwrap();
        assert_eq!(active2.id, v3.id);
        assert_eq!(mgr2.list_versions("bot1").unwrap().len(), 3);
    }
}
