use std::collections::HashMap;
use std::fs;
use std::path::{Component, Path, PathBuf};
use thiserror::Error;

use crate::models::{BotIdentityBundle, BotIdentitySpec};

#[derive(Error, Debug)]
pub enum ResolverError {
    #[error("Path traversal denied: path escapes base directory")]
    PathTraversal,
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("Invalid bundle: {0}")]
    InvalidBundle(&'static str),
}

pub fn resolve_safe_path(base_dir: &Path, rel_path: &str) -> Result<PathBuf, ResolverError> {
    let mut normalized = PathBuf::new();
    for component in Path::new(rel_path).components() {
        match component {
            Component::Normal(seg) => normalized.push(seg),
            Component::CurDir => {}
            Component::ParentDir | Component::RootDir | Component::Prefix(_) => {
                return Err(ResolverError::PathTraversal);
            }
        }
    }
    let target = base_dir.join(normalized);
    Ok(target)
}

pub struct IdentityResolver {
    root_dir: PathBuf,
}

impl IdentityResolver {
    pub fn new(root_dir: impl AsRef<Path>) -> Self {
        Self {
            root_dir: root_dir.as_ref().to_path_buf(),
        }
    }

    pub fn get_bot_dir(&self, bot_id: &str) -> Result<PathBuf, ResolverError> {
        resolve_safe_path(&self.root_dir, bot_id)
    }

    pub fn resolve(
        &self,
        bot_id: &str,
        spec: Option<&BotIdentitySpec>,
        bot_dir: Option<&Path>,
    ) -> Result<BotIdentityBundle, ResolverError> {
        let dir = match bot_dir {
            Some(d) => d.to_path_buf(),
            None => self.get_bot_dir(bot_id)?,
        };

        match spec {
            Some(s) => self.read_bundle(bot_id, s, &dir),
            None => self.resolve_legacy(bot_id, &dir),
        }
    }

    fn read_bundle(
        &self,
        bot_id: &str,
        spec: &BotIdentitySpec,
        bot_dir: &Path,
    ) -> Result<BotIdentityBundle, ResolverError> {
        let soul = self.read_asset(bot_dir, &spec.soul)?;
        let identity = self.read_asset(bot_dir, &spec.identity)?;
        let values = self.read_asset(bot_dir, &spec.values)?;

        BotIdentityBundle::new(bot_id, spec.version, soul, identity, values)
            .map_err(ResolverError::InvalidBundle)
    }

    fn resolve_legacy(
        &self,
        bot_id: &str,
        bot_dir: &Path,
    ) -> Result<BotIdentityBundle, ResolverError> {
        let soul = self.read_asset(bot_dir, "SOUL.md").unwrap_or_default();
        let identity = format!("Bot ID: {bot_id}");
        let values = String::new();

        let mut bundle = BotIdentityBundle::new(bot_id, 1, soul, identity, values)
            .map_err(ResolverError::InvalidBundle)?;
        bundle.metadata = serde_json::json!({"legacy_fallback": true});
        Ok(bundle)
    }

    fn read_asset(&self, base_dir: &Path, rel_path: &str) -> Result<String, ResolverError> {
        let safe_path = resolve_safe_path(base_dir, rel_path)?;
        if !safe_path.is_file() {
            return Ok(String::new());
        }
        let content = fs::read_to_string(safe_path)?;
        Ok(content.trim().to_string())
    }

    pub fn check_drift(
        &self,
        active_bundle: &BotIdentityBundle,
        spec: &BotIdentitySpec,
        bot_dir: Option<&Path>,
    ) -> Result<bool, ResolverError> {
        let dir = match bot_dir {
            Some(d) => d.to_path_buf(),
            None => self.get_bot_dir(&active_bundle.bot_id)?,
        };
        let current = self.read_bundle(&active_bundle.bot_id, spec, &dir)?;
        Ok(current.bundle_hash != active_bundle.bundle_hash)
    }

    pub fn diff_bundles(
        a: &BotIdentityBundle,
        b: &BotIdentityBundle,
    ) -> HashMap<&'static str, (String, String)> {
        let mut diffs = HashMap::new();
        if a.soul != b.soul {
            diffs.insert("soul", (a.soul.clone(), b.soul.clone()));
        }
        if a.identity != b.identity {
            diffs.insert("identity", (a.identity.clone(), b.identity.clone()));
        }
        if a.values != b.values {
            diffs.insert("values", (a.values.clone(), b.values.clone()));
        }
        diffs
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_path_traversal_blocked() {
        let tmp = tempdir().unwrap();
        let base = tmp.path();

        assert!(resolve_safe_path(base, "SOUL.md").is_ok());
        assert!(resolve_safe_path(base, "../etc/passwd").is_err());
        assert!(resolve_safe_path(base, "/etc/passwd").is_err());
    }

    #[test]
    fn test_resolve_bundle_and_drift() {
        let tmp = tempdir().unwrap();
        let bot_dir = tmp.path().join("arch");
        fs::create_dir_all(&bot_dir).unwrap();

        fs::write(bot_dir.join("SOUL.md"), "Architect Soul").unwrap();
        fs::write(bot_dir.join("IDENTITY.md"), "Architect Identity").unwrap();
        fs::write(bot_dir.join("VALUES.md"), "Simplicity > Complexity").unwrap();

        let resolver = IdentityResolver::new(tmp.path());
        let spec = BotIdentitySpec::default();
        let bundle = resolver.resolve("arch", Some(&spec), Some(&bot_dir)).unwrap();

        assert_eq!(bundle.bot_id, "arch");
        assert_eq!(bundle.soul, "Architect Soul");
        assert!(!resolver.check_drift(&bundle, &spec, Some(&bot_dir)).unwrap());

        // Modify file -> drift detected
        fs::write(bot_dir.join("SOUL.md"), "Architect Soul Modified").unwrap();
        assert!(resolver.check_drift(&bundle, &spec, Some(&bot_dir)).unwrap());
    }
}
