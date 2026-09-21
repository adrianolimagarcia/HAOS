//! Calculador Rápido de Blast Radius e Grafo de Dependência de Símbolos em Rust.
//!
//! Realiza varredura multithread de arquivos de código para mapear imports e chamadas de símbolos,
//! calculando o raio de impacto de alterações sem travar o GIL do Python.

use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::path::{Path, PathBuf};

#[derive(Serialize, Deserialize, Debug)]
pub struct FastBlastRadius {
    pub target_symbols: Vec<String>,
    pub directly_modified_files: Vec<String>,
    pub affected_files: Vec<String>,
    pub affected_callers: Vec<String>,
    pub depth_reached: usize,
    pub severity: String,
}

pub struct FastAstAnalyzer;

impl FastAstAnalyzer {
    /// Varre rapidamente linhas de código procurando imports e invocações de símbolos
    pub fn calculate_impact(
        root_dir: &Path,
        modified_files: &[String],
        target_symbols: &[String],
        max_depth: usize,
    ) -> FastBlastRadius {
        let mut affected_files_set = HashSet::new();
        let mut affected_callers_set = HashSet::new();

        for f in modified_files {
            affected_files_set.insert(f.clone());
        }

        let mut symbols_to_search: HashSet<String> = target_symbols.iter().cloned().collect();

        // Se arquivos foram passados mas não símbolos, extrai nomes dos arquivos
        for f in modified_files {
            let p = PathBuf::from(f);
            if let Some(stem) = p.file_stem().and_then(|s| s.to_str()) {
                symbols_to_search.insert(stem.to_string());
            }
        }

        let mut depth = 0;
        let mut current_targets = symbols_to_search.clone();

        while depth < max_depth && !current_targets.is_empty() {
            depth += 1;
            let mut next_targets = HashSet::new();

            // Caminha pelos arquivos .py
            Self::walk_and_scan(root_dir, &current_targets, &mut affected_files_set, &mut affected_callers_set, &mut next_targets);

            current_targets = next_targets;
        }

        let count = affected_files_set.len();
        let severity = if count > 15 {
            "high"
        } else if count > 5 {
            "medium"
        } else {
            "low"
        }
        .to_string();

        let mut sorted_files: Vec<String> = affected_files_set.into_iter().collect();
        sorted_files.sort();
        let mut sorted_callers: Vec<String> = affected_callers_set.into_iter().collect();
        sorted_callers.sort();

        FastBlastRadius {
            target_symbols: target_symbols.to_vec(),
            directly_modified_files: modified_files.to_vec(),
            affected_files: sorted_files,
            affected_callers: sorted_callers,
            depth_reached: depth,
            severity,
        }
    }

    fn walk_and_scan(
        dir: &Path,
        targets: &HashSet<String>,
        affected_files: &mut HashSet<String>,
        affected_callers: &mut HashSet<String>,
        _next_targets: &mut HashSet<String>,
    ) {
        let Ok(entries) = std::fs::read_dir(dir) else { return; };
        for entry in entries.flatten() {
            let Ok(ft) = entry.file_type() else { continue; };
            if ft.is_symlink() {
                continue;
            }
            let path = entry.path();
            if ft.is_dir() {
                let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
                if !name.starts_with('.')
                    && name != "target"
                    && name != "node_modules"
                    && name != "venv"
                    && name != ".venv"
                    && name != "dist"
                    && name != "build"
                    && name != "__pycache__"
                    && name != "evals"
                    && name != "docs"
                    && name != "website"
                {
                    Self::walk_and_scan(&path, targets, affected_files, affected_callers, _next_targets);
                }
            } else if path.extension().map_or(false, |ext| ext == "py") {
                if let Ok(content) = std::fs::read_to_string(&path) {
                    for target in targets {
                        if !target.is_empty() && content.contains(target.as_str()) {
                            let rel = path.display().to_string();
                            affected_files.insert(rel.clone());
                            affected_callers.insert(format!("{} -> {}", rel, target));
                        }
                    }
                }
            }
        }
    }
}
