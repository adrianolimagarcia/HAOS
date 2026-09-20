//! Gerenciador Nativo de Git Worktrees e Sandboxes Efêmeras (Zero Overhead de Subprocessos Python).

use serde::{Deserialize, Serialize};
use std::path::Path;
use std::process::Command;

#[derive(Serialize, Deserialize, Debug)]
pub struct WorktreeInfo {
    pub leaf_id: String,
    pub branch_name: String,
    pub worktree_path: String,
    pub created: bool,
}

pub struct NativeWorktreeEngine;

impl NativeWorktreeEngine {
    pub fn spawn_worktree(
        repo_dir: &Path,
        shadows_root: &Path,
        parent_id: &str,
        custom_id: Option<&str>,
        base_commit: &str,
    ) -> Result<WorktreeInfo, String> {
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();

        let clean_parent: String = parent_id.chars().filter(|c| c.is_alphanumeric() || *c == '_' || *c == '-').collect();
        let leaf_id = custom_id
            .map(|s| s.to_string())
            .unwrap_or_else(|| format!("shadow-{}-{}", clean_parent, ts));

        let branch_name = format!("shadow/{}", leaf_id);
        let worktree_dir = shadows_root.join(&leaf_id);

        if worktree_dir.exists() {
            let _ = std::fs::remove_dir_all(&worktree_dir);
        }
        let _ = std::fs::create_dir_all(shadows_root);

        let output = Command::new("git")
            .arg("-C")
            .arg(repo_dir)
            .arg("worktree")
            .arg("add")
            .arg("-b")
            .arg(&branch_name)
            .arg(&worktree_dir)
            .arg(base_commit)
            .output()
            .map_err(|e| format!("Falha ao invocar git worktree: {e}"))?;

        if !output.status.success() {
            let err = String::from_utf8_lossy(&output.stderr);
            return Err(format!("git worktree add falhou: {err}"));
        }

        Ok(WorktreeInfo {
            leaf_id,
            branch_name,
            worktree_path: worktree_dir.display().to_string(),
            created: true,
        })
    }

    pub fn discard_worktree(repo_dir: &Path, worktree_path: &Path, branch_name: Option<&str>) -> Result<bool, String> {
        if worktree_path.exists() {
            let _ = Command::new("git")
                .arg("-C")
                .arg(repo_dir)
                .arg("worktree")
                .arg("remove")
                .arg("--force")
                .arg(worktree_path)
                .output();

            let _ = std::fs::remove_dir_all(worktree_path);
        }

        if let Some(b) = branch_name {
            let _ = Command::new("git")
                .arg("-C")
                .arg(repo_dir)
                .arg("branch")
                .arg("-D")
                .arg(b)
                .output();
        }

        Ok(true)
    }
}
