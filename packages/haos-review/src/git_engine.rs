//! Extração de diffs via comando Git do host ou staged/unstaged
use std::process::Command;

pub struct GitEngine;

impl GitEngine {
    pub fn get_diff(target: Option<&str>, from: Option<&str>, to: Option<&str>) -> Result<String, String> {
        let mut cmd = Command::new("git");
        cmd.arg("diff");

        if let (Some(f), Some(t)) = (from, to) {
            cmd.arg(format!("{}..{}", f, t));
        } else if let Some(t) = target {
            cmd.arg(t);
        } else {
            // Por padrão compara alterações na working tree (staged + unstaged)
            cmd.arg("HEAD");
        }

        let output = cmd.output().map_err(|e| format!("Falha ao executar git diff: {e}"))?;
        if !output.status.success() {
            let err = String::from_utf8_lossy(&output.stderr);
            return Err(format!("git diff retornou erro: {err}"));
        }

        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    }
}
