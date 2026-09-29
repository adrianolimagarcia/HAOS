use crate::crypto::{compute_sha256, generate_id, now_timestamp};
use crate::models::{IdentityVersion, LeafIdentitySnapshot};

pub fn build_temporary_soul(
    parent_soul: &str,
    task_description: &str,
    constraints: &[String],
    council_context: Option<&str>,
) -> String {
    let mut parts = Vec::new();
    if !parent_soul.trim().is_empty() {
        parts.push(parent_soul.trim().to_string());
    }
    if !task_description.trim().is_empty() {
        parts.push(format!("## Mission Focus\n{}", task_description.trim()));
    }
    if !constraints.is_empty() {
        let formatted: Vec<String> = constraints
            .iter()
            .filter(|c| !c.trim().is_empty())
            .map(|c| format!("- {}", c.trim()))
            .collect();
        if !formatted.is_empty() {
            parts.push(format!("## Mission Constraints\n{}", formatted.join("\n")));
        }
    }
    if let Some(council) = council_context {
        if !council.trim().is_empty() {
            parts.push(format!("## Council Context\n{}", council.trim()));
        }
    }
    parts.join("\n\n")
}

pub fn create_leaf_identity_snapshot(
    leaf_id: Option<&str>,
    parent_bot_id: &str,
    identity_version: &IdentityVersion,
    task_description: &str,
    constraints: &[String],
    council_id: Option<&str>,
    council_session_id: Option<&str>,
    model: &str,
    toolset_hash: &str,
    correlation_id: Option<&str>,
    causation_id: Option<&str>,
) -> LeafIdentitySnapshot {
    let id = leaf_id
        .map(|s| s.to_string())
        .unwrap_or_else(|| generate_id("leaf"));

    let parent_soul = identity_version
        .bundle
        .as_ref()
        .map(|b| b.soul.as_str())
        .unwrap_or("");

    let temporary_soul = build_temporary_soul(
        parent_soul,
        task_description,
        constraints,
        council_id,
    );

    let temporary_soul_hash = compute_sha256(&temporary_soul);

    LeafIdentitySnapshot {
        leaf_id: id,
        parent_bot_id: parent_bot_id.to_string(),
        identity_version_id: identity_version.id.clone(),
        bot_identity_version: identity_version.version,
        identity_bundle_hash: identity_version.bundle_hash.clone(),
        temporary_soul,
        temporary_soul_hash,
        prompt_hash: String::new(),
        toolset_hash: toolset_hash.to_string(),
        memory_snapshot_ref: None,
        council_id: council_id.map(|s| s.to_string()),
        council_session_id: council_session_id.map(|s| s.to_string()),
        model: model.to_string(),
        created_at: now_timestamp(),
        expires_at: None,
        causation_id: causation_id.map(|s| s.to_string()),
        correlation_id: correlation_id.map(|s| s.to_string()),
        schema_version: 1,
        metadata: serde_json::Value::Object(serde_json::Map::new()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::BotIdentityBundle;

    #[test]
    fn test_temporary_soul_assembly() {
        let soul = build_temporary_soul(
            "Architect Core",
            "Build Rust engine",
            &["No panics".to_string()],
            Some("Council #4"),
        );

        assert!(soul.contains("Architect Core"));
        assert!(soul.contains("## Mission Focus\nBuild Rust engine"));
        assert!(soul.contains("## Mission Constraints\n- No panics"));
        assert!(soul.contains("## Council Context\nCouncil #4"));
    }

    #[test]
    fn test_snapshot_creation() {
        let bundle = BotIdentityBundle::new("arch", 1, "Core", "Id", "Val").unwrap();
        let ver = IdentityVersion {
            id: "v-1".to_string(),
            bot_id: "arch".to_string(),
            version: 1,
            bundle_hash: bundle.bundle_hash.clone(),
            parent_id: None,
            bundle: Some(bundle),
            status: "active".to_string(),
            created_at: now_timestamp(),
            schema_version: 1,
            metadata: serde_json::Value::Null,
        };

        let snap = create_leaf_identity_snapshot(
            Some("leaf-1"),
            "arch",
            &ver,
            "Task 1",
            &[],
            None,
            None,
            "gemini-3.8",
            "toolset-1",
            None,
            None,
        );

        assert_eq!(snap.leaf_id, "leaf-1");
        assert_eq!(snap.identity_version_id, "v-1");
        assert_eq!(snap.bot_identity_version, 1);
        assert!(!snap.temporary_soul_hash.is_empty());
    }
}
