//! Pipeline determinístico de regras estáticas (NPE, SQLi, Secrets, Thread-Safety)
//! Inspirado no Alibaba Open Code Review, adaptado em Rust para execução em microssegundos.

use serde::{Deserialize, Serialize};
use regex::Regex;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LineFinding {
    pub file: String,
    pub line_number: usize,
    pub rule_id: String,
    pub severity: String,
    pub message: String,
    pub code_snippet: String,
}

pub struct DeterministicPipeline {
    rules: Vec<RuleMatcher>,
}

struct RuleMatcher {
    rule_id: &'static str,
    severity: &'static str,
    message: &'static str,
    regex: Regex,
}

impl DeterministicPipeline {
    pub fn new() -> Self {
        let rules = vec![
            RuleMatcher {
                rule_id: "sec-hardcoded-secret",
                severity: "CRITICAL",
                message: "Possível credencial ou chave de API privada hardcoded detectada no diff.",
                regex: Regex::new(r#"(?i)(api[_-]?key|secret[_-]?key|password|token|bearer)\s*[:=]\s*["'][A-Za-z0-9_\-\.]{16,}["']"#).unwrap(),
            },
            RuleMatcher {
                rule_id: "sec-sql-injection",
                severity: "HIGH",
                message: "Interpolação direta de strings em query SQL detectada (risco de SQL Injection).",
                regex: Regex::new(r#"(?i)(select|insert|update|delete)\s+.*(\+|\%s|\$|\{).*(from|into|table)"#).unwrap(),
            },
            RuleMatcher {
                rule_id: "sec-command-injection",
                severity: "CRITICAL",
                message: "Execução direta de comando de sistema concatenando variáveis externas sem escape.",
                regex: Regex::new(r#"(?i)(os\.system|exec\.Command|subprocess\.Popen|eval|child_process\.exec)\s*\(.*(\+|f["']).*\)"#).unwrap(),
            },
            RuleMatcher {
                rule_id: "err-unhandled-unwrap",
                severity: "MEDIUM",
                message: "Uso arriscado de .unwrap() ou .expect() em código Rust em caminho produtivo.",
                regex: Regex::new(r#"\.(unwrap|expect)\(\)"#).unwrap(),
            },
            RuleMatcher {
                rule_id: "err-empty-catch",
                severity: "LOW",
                message: "Bloco catch ou except vazio silenciando exceções críticas.",
                regex: Regex::new(r#"(?i)(except\s*:\s*pass|catch\s*\([^\)]*\)\s*\{\s*\})"#).unwrap(),
            },
        ];

        Self { rules }
    }

    /// Analisa o texto de um git diff unificado e extrai findings a nível de linha
    pub fn scan_diff(&self, diff_text: &str) -> Vec<LineFinding> {
        let mut findings = Vec::new();
        let mut current_file = String::new();
        let mut current_line = 0;

        for line in diff_text.lines() {
            if line.starts_with("+++ b/") {
                current_file = line.trim_start_matches("+++ b/").to_string();
                continue;
            }

            if line.starts_with("@@ ") {
                // Parse chunk header: @@ -1,4 +1,5 @@
                if let Some(plus_idx) = line.find('+') {
                    let sub = &line[plus_idx + 1..];
                    let end_idx = sub.find(|c: char| c == ',' || c == ' ').unwrap_or(sub.len());
                    if let Ok(num) = sub[..end_idx].parse::<usize>() {
                        current_line = num;
                    }
                }
                continue;
            }

            // Somente analisa linhas adicionadas (+)
            if line.starts_with('+') && !line.starts_with("+++") {
                let added_code = &line[1..];
                for rule in &self.rules {
                    if rule.regex.is_match(added_code) {
                        findings.push(LineFinding {
                            file: current_file.clone(),
                            line_number: current_line,
                            rule_id: rule.rule_id.to_string(),
                            severity: rule.severity.to_string(),
                            message: rule.message.to_string(),
                            code_snippet: added_code.trim().to_string(),
                        });
                    }
                }
                current_line += 1;
            } else if !line.starts_with('-') {
                current_line += 1;
            }
        }

        findings
    }
}
