//! Fast native file engine for tools (search_files, read_file, patch)
//! Ultra-fast ripgrep-like search and streaming I/O with Tokio/mmap.

use std::path::{Path, PathBuf};
use serde::{Deserialize, Serialize};
use walkdir::WalkDir;
use regex::Regex;

#[derive(Debug, Serialize, Deserialize)]
pub struct SearchMatch {
    pub file: String,
    pub line_number: usize,
    pub line_text: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct SearchFilesResult {
    pub total_matches: usize,
    pub files_searched: usize,
    pub matches: Vec<SearchMatch>,
    pub truncated: bool,
}

pub struct FastFileEngine;

impl FastFileEngine {
    pub fn search_files(
        root_dir: &Path,
        pattern_str: &str,
        file_glob: Option<&str>,
        max_matches: usize,
    ) -> Result<SearchFilesResult, String> {
        let regex = Regex::new(pattern_str).map_err(|e| format!("Invalid regex: {e}"))?;
        let glob_pattern = file_glob.map(|g| g.trim_start_matches('*'));

        let mut matches = Vec::new();
        let mut files_searched = 0;
        let mut truncated = false;

        for entry in WalkDir::new(root_dir)
            .follow_links(false)
            .into_iter()
            .filter_entry(|e| {
                let name = e.file_name().to_string_lossy();
                // Ignora diretórios pesados comuns
                !(name.starts_with('.') && name != ".")
                    && name != "node_modules"
                    && name != "target"
                    && name != "venv"
                    && name != ".venv"
                    && name != ".git"
            })
            .flatten()
        {
            if !entry.file_type().is_file() {
                continue;
            }

            let path = entry.path();
            if let Some(ext) = glob_pattern {
                if !path.to_string_lossy().ends_with(ext) {
                    continue;
                }
            }

            files_searched += 1;

            if let Ok(content) = std::fs::read_to_string(path) {
                for (idx, line) in content.lines().enumerate() {
                    if regex.is_match(line) {
                        matches.push(SearchMatch {
                            file: path.to_string_lossy().to_string(),
                            line_number: idx + 1,
                            line_text: line.chars().take(200).collect(),
                        });

                        if matches.len() >= max_matches {
                            truncated = true;
                            break;
                        }
                    }
                }
            }

            if truncated {
                break;
            }
        }

        let total = matches.len();
        Ok(SearchFilesResult {
            total_matches: total,
            files_searched,
            matches,
            truncated,
        })
    }

    pub fn read_file(path: &Path, offset: Option<usize>, limit: Option<usize>) -> Result<serde_json::Value, String> {
        if !path.exists() {
            return Err(format!("File does not exist: {}", path.display()));
        }

        let metadata = path.metadata().map_err(|e| format!("Failed to read metadata: {e}"))?;
        let file_size = metadata.len();

        let content = std::fs::read_to_string(path).map_err(|e| format!("Failed to read file: {e}"))?;
        let lines: Vec<&str> = content.lines().collect();
        let total_lines = lines.len();

        let start_line = offset.unwrap_or(1).saturating_sub(1);
        let count = limit.unwrap_or(2000);

        let slice = if start_line < total_lines {
            lines[start_line..lines.len().min(start_line + count)].join("\n")
        } else {
            String::new()
        };

        Ok(serde_json::json!({
            "content": slice,
            "total_lines": total_lines,
            "start_line": start_line + 1,
            "lines_returned": slice.lines().count(),
            "size_bytes": file_size,
        }))
    }
}
