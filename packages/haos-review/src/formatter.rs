//! Formatação e síntese de relatórios de revisão
use serde::{Deserialize, Serialize};
use crate::rules::LineFinding;

#[derive(Debug, Serialize, Deserialize)]
pub struct ReviewReport {
    pub total_findings: usize,
    pub status: String,
    pub findings: Vec<LineFinding>,
    pub summary: String,
}

impl ReviewReport {
    pub fn build(findings: Vec<LineFinding>) -> Self {
        let total = findings.len();
        let status = if findings.iter().any(|f| f.severity == "CRITICAL") {
            "REJECTED".to_string()
        } else if total > 0 {
            "CHANGES_REQUESTED".to_string()
        } else {
            "APPROVED".to_string()
        };

        let summary = match status.as_str() {
            "APPROVED" => "✅ Código aprovado! Nenhuma violação estática crítica detectada.".to_string(),
            "CHANGES_REQUESTED" => format!("⚠️ Foram encontradas {} observações de boas práticas/risco.", total),
            _ => format!("🛑 BLOQUEADO: {} violações críticas identificadas que comprometem a segurança ou estabilidade.", total),
        };

        Self {
            total_findings: total,
            status,
            findings,
            summary,
        }
    }
}
