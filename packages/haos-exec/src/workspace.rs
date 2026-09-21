use std::{
    fs,
    path::{Path, PathBuf},
};
pub fn resolve(root: &Path, requested: &str) -> Result<PathBuf, String> {
    if Path::new(requested).is_absolute() {
        return Err("absolute cwd is not allowed".into());
    }
    let canonical_root = fs::canonicalize(root).map_err(|e| e.to_string())?;
    let candidate = canonical_root.join(requested);
    for component in Path::new(requested).components() {
        if let std::path::Component::Normal(part) = component {
            let p = canonical_root.join(part);
            if fs::symlink_metadata(&p)
                .map_err(|e| e.to_string())?
                .file_type()
                .is_symlink()
            {
                return Err("symlink cwd is not allowed".into());
            }
        }
    }
    let cwd = fs::canonicalize(candidate).map_err(|e| e.to_string())?;
    if cwd == canonical_root || cwd.starts_with(&canonical_root) {
        Ok(cwd)
    } else {
        Err("cwd escapes HERMES_EXEC_ROOT".into())
    }
}
