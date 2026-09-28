//! Explicit profile/data-directory binding for the read-only control plane.
//!
//! This module is deliberately pure: it never reads environment variables, the filesystem,
//! or a process-global default. Callers must provide both the profile identity and its already
//! resolved home before constructing server state.

use std::path::{Component, Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedProfile {
    profile: String,
    data_dir: PathBuf,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProfileResolutionError {
    MissingProfile,
    MissingDataDir,
    InvalidProfile,
    DataDirMustBeAbsolute,
}

impl ResolvedProfile {
    pub fn profile(&self) -> &str {
        &self.profile
    }

    pub fn data_dir(&self) -> &Path {
        &self.data_dir
    }
}

/// Resolve one immutable profile binding from explicit inputs.
///
/// No environment variable or global/default home is consulted here. This is the boundary that
/// must run before `AppState` is constructed, so a later profile switch cannot change the paths
/// used by request handlers.
pub fn resolve_profile_data_dir(
    profile: Option<&str>,
    data_dir: Option<&Path>,
) -> Result<ResolvedProfile, ProfileResolutionError> {
    let profile = profile.ok_or(ProfileResolutionError::MissingProfile)?;
    if !valid_profile(profile) {
        return Err(ProfileResolutionError::InvalidProfile);
    }

    let data_dir = data_dir.ok_or(ProfileResolutionError::MissingDataDir)?;
    if !data_dir.is_absolute() {
        return Err(ProfileResolutionError::DataDirMustBeAbsolute);
    }

    Ok(ResolvedProfile {
        profile: profile.to_owned(),
        data_dir: normalize_path(data_dir),
    })
}

fn valid_profile(profile: &str) -> bool {
    !profile.is_empty()
        && profile != "."
        && profile != ".."
        && !profile.contains('/')
        && !profile.contains('\\')
        && !profile.contains('\0')
}

fn normalize_path(path: &Path) -> PathBuf {
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            Component::RootDir | Component::Prefix(_) => normalized.push(component.as_os_str()),
            Component::CurDir => {}
            Component::ParentDir => {
                let _ = normalized.pop();
            }
            Component::Normal(part) => normalized.push(part),
        }
    }
    if normalized.as_os_str().is_empty() {
        PathBuf::from("/")
    } else {
        normalized
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn profile_binding_is_explicit_and_normalized() {
        let resolved =
            resolve_profile_data_dir(Some("alpha"), Some(Path::new("/tmp/haos/alpha/../alpha")))
                .unwrap();
        assert_eq!(resolved.profile(), "alpha");
        assert_eq!(resolved.data_dir(), Path::new("/tmp/haos/alpha"));
        assert_eq!(
            resolve_profile_data_dir(None, Some(Path::new("/tmp/haos"))),
            Err(ProfileResolutionError::MissingProfile)
        );
        assert_eq!(
            resolve_profile_data_dir(Some("alpha"), None),
            Err(ProfileResolutionError::MissingDataDir)
        );
    }

    #[test]
    fn profile_a_b_a_never_reuses_the_previous_binding() {
        let alpha = resolve_profile_data_dir(Some("alpha"), Some(Path::new("/srv/alpha"))).unwrap();
        let beta = resolve_profile_data_dir(Some("beta"), Some(Path::new("/srv/beta"))).unwrap();
        let alpha_again =
            resolve_profile_data_dir(Some("alpha"), Some(Path::new("/srv/alpha"))).unwrap();

        assert_ne!(alpha, beta);
        assert_eq!(alpha, alpha_again);
        assert_eq!(alpha.data_dir(), Path::new("/srv/alpha"));
        assert_eq!(beta.data_dir(), Path::new("/srv/beta"));
    }
}
