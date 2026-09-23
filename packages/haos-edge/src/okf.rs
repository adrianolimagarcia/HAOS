use regex::Regex;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};
use walkdir::WalkDir;

/// Retorna data atual no formato YYYY-MM-DD em UTC
fn get_today_utc() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    // Dias desde 1970-01-01
    let mut days = (secs / 86400) as i64;
    // Algoritmo civil date de Howard Hinnant
    days += 719468;
    let era = if days >= 0 { days } else { days - 146096 } / 146097;
    let doe = (days - era * 146097) as u32;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = (yoe as i64) + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{:04}-{:02}-{:02}", y, m, d)
}

fn get_now_rfc3339() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs();
    let today = get_today_utc();
    let day_secs = secs % 86400;
    let hours = day_secs / 3600;
    let minutes = (day_secs % 3600) / 60;
    let seconds = day_secs % 60;
    format!("{}T{:02}:{:02}:{:02}Z", today, hours, minutes, seconds)
}

/// Representação do ciclo de vida no OKF v0.2
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DocumentStatus {
    Draft,
    Stable,
    Deprecated,
}

impl Default for DocumentStatus {
    fn default() -> Self {
        DocumentStatus::Stable
    }
}

/// Metadado de proveniência de geração (generated: { by: "...", at: "..." })
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct GeneratedInfo {
    pub by: Option<String>,
    pub at: Option<String>,
}

/// Metadado de verificação humana ou de processo (verified: [{ by: "...", at: "..." }])
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct VerifiedInfo {
    pub by: String,
    pub at: Option<String>,
}

/// Frontmatter OKF v0.2
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct OkfFrontmatter {
    pub title: Option<String>,
    #[serde(rename = "type")]
    pub doc_type: Option<String>,
    #[serde(default)]
    pub tags: Vec<String>,
    pub owner: Option<String>,
    #[serde(default)]
    pub status: DocumentStatus,
    pub stale_after: Option<String>,
    pub generated: Option<GeneratedInfo>,
    #[serde(default)]
    pub verified: Vec<VerifiedInfo>,

    // Attested Computation fields (v0.2)
    pub runtime: Option<String>,
    pub computation: Option<String>,
    pub executor: Option<serde_yaml::Value>,
    pub attester: Option<serde_yaml::Value>,
    pub parameters: Option<HashMap<String, serde_yaml::Value>>,

    #[serde(flatten)]
    pub extra: HashMap<String, serde_yaml::Value>,
}

/// Documento OKF com caminho, metadados e corpo
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OkfDocument {
    pub filepath: PathBuf,
    pub rel_path: String,
    pub frontmatter: OkfFrontmatter,
    pub body: String,
    pub is_stale: bool,
    pub trust_score: f32,
}

/// Resultado do Linter OKF
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct LintReport {
    pub total_files: usize,
    pub valid_files: usize,
    pub missing_frontmatter: Vec<String>,
    pub invalid_frontmatter: Vec<(String, String)>,
    pub deprecated_docs: Vec<String>,
    pub stale_docs: Vec<(String, String)>, // (path, stale_after)
    pub broken_links: Vec<(String, String, String)>, // (file, link_text, target)
    pub unknown_types: Vec<(String, String)>, // (file, type)
}

/// Receipt de Attested Computation
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AttestedReceipt {
    pub concept_path: String,
    pub runtime: String,
    pub executed_command: String,
    pub parameters: HashMap<String, String>,
    pub executed_at: String,
    pub stdout: String,
    pub stderr: String,
    pub exit_code: i32,
    pub stdout_sha256: String,
    pub computation_sha256: String,
    pub verified: bool,
}

/// Parser de frontmatter YAML
pub fn parse_okf_file(path: &Path, bundle_root: &Path) -> Result<OkfDocument, String> {
    let content = fs::read_to_string(path).map_err(|e| format!("Failed to read file: {e}"))?;
    let rel_path = path
        .strip_prefix(bundle_root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/");

    let today = get_today_utc();

    if !content.starts_with("---") {
        return Err("Missing YAML frontmatter prefix ('---')".to_string());
    }

    let parts: Vec<&str> = content.splitn(3, "---").collect();
    if parts.len() < 3 {
        return Err("Unclosed YAML frontmatter ('---')".to_string());
    }

    let yaml_str = parts[1];
    let body = parts[2].trim().to_string();

    // Suporte flexível para verified como lista ou item único
    // Também pré-tratar YAML caso o usuário tenha campos variados
    let mut fm: OkfFrontmatter = match serde_yaml::from_str::<OkfFrontmatter>(yaml_str) {
        Ok(f) => f,
        Err(_) => {
            // Tentar deserializar genérico e converter verified se for objeto único
            let val: serde_yaml::Value =
                serde_yaml::from_str(yaml_str).map_err(|e| format!("YAML parse error: {e}"))?;

            let mut val_clone = val.clone();
            if let serde_yaml::Value::Mapping(ref mut map) = val_clone {
                let verified_key = serde_yaml::Value::String("verified".to_string());
                if let Some(v) = map.get(&verified_key) {
                    if v.is_mapping() {
                        let single = v.clone();
                        map.insert(verified_key, serde_yaml::Value::Sequence(vec![single]));
                    }
                }
            }
            serde_yaml::from_value(val_clone)
                .map_err(|e| format!("Frontmatter structure error: {e}"))?
        }
    };

    if fm.title.is_none() {
        fm.title = path.file_stem().map(|s| s.to_string_lossy().to_string());
    }

    // Calcular staleness (stale_after <= hoje)
    let is_stale = if let Some(ref sa) = fm.stale_after {
        let clean_sa = sa.trim();
        let date_part = if clean_sa.len() >= 10 {
            &clean_sa[..10]
        } else {
            clean_sa
        };
        date_part <= today.as_str()
    } else {
        false
    };

    // Calcular trust_score
    // Base = 1.0 (se estável) ou 0.5 (se draft) ou 0.1 (se deprecated)
    let mut trust: f32 = match fm.status {
        DocumentStatus::Stable => 1.0,
        DocumentStatus::Draft => 0.5,
        DocumentStatus::Deprecated => 0.1,
    };

    // Se stale, penaliza
    if is_stale {
        trust *= 0.3;
    }

    // Verificar se tem humano na lista de verificação (aumenta o score)
    let mut has_human = false;
    let mut has_verifier = false;
    for v in &fm.verified {
        has_verifier = true;
        if v.by.to_lowercase().starts_with("human:") || v.by.to_lowercase().contains("human") {
            has_human = true;
        }
    }

    if has_human {
        trust += 2.0;
    } else if has_verifier {
        trust += 0.5;
    }

    Ok(OkfDocument {
        filepath: path.to_path_buf(),
        rel_path,
        frontmatter: fm,
        body,
        is_stale,
        trust_score: trust,
    })
}

/// Varre o bundle OKF e executa o linter
pub fn lint_bundle(bundle_dir: &Path) -> LintReport {
    let mut report = LintReport::default();
    let _today = get_today_utc();

    let recognized_types: [&str; 9] = [
        "metric",
        "licao",
        "lesson",
        "api-contract",
        "architecture",
        "runbook",
        "concept",
        "attested computation",
        "attested_computation",
    ];

    let link_re = Regex::new(r"\[([^\]]+)\]\(([^)]+\.md)\)").unwrap();

    for entry in WalkDir::new(bundle_dir).into_iter().filter_map(|e| e.ok()) {
        let p = entry.path();
        if p.extension().map_or(false, |ext| ext == "md") {
            report.total_files += 1;
            let rel = p
                .strip_prefix(bundle_dir)
                .unwrap_or(p)
                .to_string_lossy()
                .replace('\\', "/");

            match parse_okf_file(p, bundle_dir) {
                Ok(doc) => {
                    report.valid_files += 1;

                    // Checar status
                    if doc.frontmatter.status == DocumentStatus::Deprecated {
                        report.deprecated_docs.push(rel.clone());
                    }

                    // Checar stale
                    if doc.is_stale {
                        if let Some(sa) = doc.frontmatter.stale_after {
                            report.stale_docs.push((rel.clone(), sa));
                        }
                    }

                    // Checar tipo
                    if let Some(ref dt) = doc.frontmatter.doc_type {
                        let dt_low = dt.to_lowercase();
                        if !recognized_types.iter().any(|t| t == &dt_low) {
                            report.unknown_types.push((rel.clone(), dt.clone()));
                        }
                    }

                    // Checar links quebrados no corpo
                    for cap in link_re.captures_iter(&doc.body) {
                        let link_text = cap.get(1).map_or("", |m| m.as_str()).to_string();
                        let target = cap.get(2).map_or("", |m| m.as_str()).to_string();

                        if target.starts_with("http://") || target.starts_with("https://") {
                            continue;
                        }

                        // Normalizar destino relativo ao diretório do arquivo ou relativo ao bundle
                        let target_path_file = p.parent().unwrap_or(bundle_dir).join(&target);
                        let target_path_bundle = bundle_dir.join(&target);

                        if !target_path_file.exists() && !target_path_bundle.exists() {
                            report.broken_links.push((rel.clone(), link_text, target));
                        }
                    }
                }
                Err(e) => {
                    if e.contains("Missing YAML frontmatter") {
                        report.missing_frontmatter.push(rel);
                    } else {
                        report.invalid_frontmatter.push((rel, e));
                    }
                }
            }
        }
    }

    report
}

/// Consulta no repositório OKF com filtros e ordenação por confiança
pub fn query_okf(
    bundle_dir: &Path,
    query: &str,
    include_stale: bool,
    include_deprecated: bool,
    limit: usize,
) -> Vec<OkfDocument> {
    let q_clean = query.trim().to_lowercase();
    let mut docs: Vec<OkfDocument> = Vec::new();

    for entry in WalkDir::new(bundle_dir).into_iter().filter_map(|e| e.ok()) {
        let p = entry.path();
        if p.extension().map_or(false, |ext| ext == "md") {
            if let Ok(doc) = parse_okf_file(p, bundle_dir) {
                if !include_deprecated && doc.frontmatter.status == DocumentStatus::Deprecated {
                    continue;
                }
                if !include_stale && doc.is_stale {
                    continue;
                }

                // Filtrar por query se fornecida
                if !q_clean.is_empty() {
                    let title_match = doc
                        .frontmatter
                        .title
                        .as_ref()
                        .map_or(false, |t| t.to_lowercase().contains(&q_clean));
                    let tag_match = doc
                        .frontmatter
                        .tags
                        .iter()
                        .any(|t| t.to_lowercase().contains(&q_clean));
                    let body_match = doc.body.to_lowercase().contains(&q_clean);
                    let path_match = doc.rel_path.to_lowercase().contains(&q_clean);

                    if !title_match && !tag_match && !body_match && !path_match {
                        continue;
                    }
                }

                docs.push(doc);
            }
        }
    }

    // Ordenar decrescente por trust_score
    docs.sort_by(|a, b| {
        b.trust_score
            .partial_cmp(&a.trust_score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    if limit > 0 && docs.len() > limit {
        docs.truncate(limit);
    }

    docs
}

/// Execução auditada de Attested Computation (v0.2)
/// Lê o arquivo de conceito OKF, valida runtime/parâmetros, executa determinísticamente
/// e gera um receipt com checksums auditáveis.
pub fn execute_attestation(
    bundle_dir: &Path,
    concept_rel_path: &str,
    input_params: &HashMap<String, String>,
) -> Result<AttestedReceipt, String> {
    let concept_file = bundle_dir.join(concept_rel_path);
    if !concept_file.exists() {
        return Err(format!(
            "Concept file not found: {}",
            concept_file.display()
        ));
    }

    let doc = parse_okf_file(&concept_file, bundle_dir)
        .map_err(|e| format!("Failed to parse concept file: {e}"))?;

    let is_attested = doc.frontmatter.doc_type.as_ref().map_or(false, |dt| {
        let low = dt.to_lowercase();
        low == "attested computation" || low == "attested_computation"
    });

    if !is_attested {
        return Err(format!(
            "Concept is not of type 'Attested Computation' (found: {:?})",
            doc.frontmatter.doc_type
        ));
    }

    let runtime = doc
        .frontmatter
        .runtime
        .clone()
        .unwrap_or_else(|| "bash".to_string());

    // Obter o comando/script sancionado
    let mut command_script = String::new();
    if let Some(ref comp_path) = doc.frontmatter.computation {
        let full_comp = bundle_dir.join(comp_path);
        if full_comp.exists() {
            command_script = fs::read_to_string(&full_comp)
                .map_err(|e| format!("Failed to read computation file: {e}"))?;
        } else {
            return Err(format!(
                "Computation file not found: {}",
                full_comp.display()
            ));
        }
    } else {
        // Extrair do corpo markdown (fenced block sob `# Computation`)
        let lines: Vec<&str> = doc.body.lines().collect();
        let mut inside_block = false;
        for line in lines {
            let trimmed = line.trim();
            if trimmed.starts_with("```") {
                if inside_block {
                    break;
                } else {
                    inside_block = true;
                    continue;
                }
            }
            if inside_block {
                command_script.push_str(line);
                command_script.push('\n');
            }
        }
    }

    if command_script.trim().is_empty() {
        // Se ainda vazio, usar o corpo todo caso seja simples
        command_script = doc.body.clone();
    }

    // Validar parâmetros declarados
    // Se o frontmatter tiver schema de regex nos parâmetros, validar
    if let Some(ref declared_params) = doc.frontmatter.parameters {
        for (k, v) in declared_params {
            if let Some(val_str) = input_params.get(k) {
                // Se houver um padrão regex declarado
                if let Some(schema_map) = v.as_mapping() {
                    let pattern_key = serde_yaml::Value::String("pattern".to_string());
                    if let Some(pattern_val) = schema_map.get(&pattern_key).and_then(|p| p.as_str())
                    {
                        let re = Regex::new(pattern_val)
                            .map_err(|e| format!("Invalid parameter regex pattern: {e}"))?;
                        if !re.is_match(val_str) {
                            return Err(format!(
                                "Parameter '{}' with value '{}' fails regex pattern '{}'",
                                k, val_str, pattern_val
                            ));
                        }
                    }
                }
            }
        }
    }

    // Interpolar parâmetros de forma segura (ex: {{param}} ou $PARAM)
    let mut final_script = command_script.clone();
    for (k, v) in input_params {
        // Validação anti injection básica nos parâmetros
        if v.contains("`")
            || v.contains("$(")
            || v.contains(";")
            || v.contains("&")
            || v.contains("|")
        {
            return Err(format!(
                "Parameter '{}' contains prohibited shell metacharacters",
                k
            ));
        }
        let token = format!("{{{{{}}}}}", k);
        final_script = final_script.replace(&token, v);
    }

    // Checksum da computação antes de rodar
    let mut comp_hasher = Sha256::new();
    comp_hasher.update(final_script.as_bytes());
    let comp_sha = format!("{:x}", comp_hasher.finalize());

    let now_str = get_now_rfc3339();

    // Executar conforme runtime
    let (status, stdout, stderr) = match runtime.to_lowercase().as_str() {
        "bash" | "sh" => {
            let output = Command::new("bash")
                .arg("-c")
                .arg(&final_script)
                .output()
                .map_err(|e| format!("Execution failed: {e}"))?;
            (
                output.status.code().unwrap_or(-1),
                String::from_utf8_lossy(&output.stdout).to_string(),
                String::from_utf8_lossy(&output.stderr).to_string(),
            )
        }
        "python" | "python3" => {
            let output = Command::new("python3")
                .arg("-c")
                .arg(&final_script)
                .output()
                .map_err(|e| format!("Execution failed: {e}"))?;
            (
                output.status.code().unwrap_or(-1),
                String::from_utf8_lossy(&output.stdout).to_string(),
                String::from_utf8_lossy(&output.stderr).to_string(),
            )
        }
        _ => {
            return Err(format!("Unsupported computation runtime: {runtime}"));
        }
    };

    // Checksum da saída
    let mut out_hasher = Sha256::new();
    out_hasher.update(stdout.as_bytes());
    let stdout_sha = format!("{:x}", out_hasher.finalize());

    let receipt = AttestedReceipt {
        concept_path: concept_rel_path.to_string(),
        runtime,
        executed_command: final_script.trim().to_string(),
        parameters: input_params.clone(),
        executed_at: now_str,
        stdout: stdout.trim().to_string(),
        stderr: stderr.trim().to_string(),
        exit_code: status,
        stdout_sha256: stdout_sha,
        computation_sha256: comp_sha,
        verified: status == 0,
    };

    Ok(receipt)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    struct TempDir(PathBuf);
    impl TempDir {
        fn new() -> Self {
            let p = std::env::temp_dir().join(format!(
                "okf_test_{}",
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            let _ = fs::create_dir_all(&p);
            TempDir(p)
        }
        fn path(&self) -> &Path {
            &self.0
        }
    }
    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn test_parse_okf_frontmatter() {
        let dir = TempDir::new();
        let file_path = dir.path().join("metric.md");
        let content = r#"---
title: "System Memory Available"
type: "metric"
tags: [memory, telemetry, linux]
status: stable
stale_after: "2029-12-31"
generated:
  by: "reference_agent/gemini"
  at: "2026-06-30T14:00:00Z"
verified:
  - by: "human:adriano"
    at: "2026-07-01T16:00:00Z"
---
# Content here
Some markdown body with [link](other.md).
"#;
        let mut file = fs::File::create(&file_path).unwrap();
        file.write_all(content.as_bytes()).unwrap();

        let doc = parse_okf_file(&file_path, dir.path()).unwrap();
        assert_eq!(
            doc.frontmatter.title.as_deref(),
            Some("System Memory Available")
        );
        assert_eq!(doc.frontmatter.status, DocumentStatus::Stable);
        assert_eq!(doc.is_stale, false);
        assert!(doc.trust_score >= 2.0); // Verificado por humano
    }

    #[test]
    fn test_stale_and_deprecated() {
        let dir = TempDir::new();
        let file_path = dir.path().join("old.md");
        let content = r#"---
title: "Old Metric"
type: "metric"
status: deprecated
stale_after: "2020-01-01"
---
Obsolete
"#;
        fs::write(&file_path, content).unwrap();

        let doc = parse_okf_file(&file_path, dir.path()).unwrap();
        assert_eq!(doc.frontmatter.status, DocumentStatus::Deprecated);
        assert_eq!(doc.is_stale, true);
        assert!(doc.trust_score < 0.1);
    }

    #[test]
    fn test_lint_bundle() {
        let dir = TempDir::new();
        // Arquivo valido
        fs::write(
            dir.path().join("valid.md"),
            "---\ntitle: Ok\ntype: metric\n---\nBody",
        )
        .unwrap();

        // Arquivo sem frontmatter
        fs::write(dir.path().join("no_fm.md"), "Just plain text").unwrap();

        // Arquivo com link quebrado
        fs::write(
            dir.path().join("broken_link.md"),
            "---\ntitle: Broken\ntype: metric\n---\nSee [Missing](non_existent.md)",
        )
        .unwrap();

        let report = lint_bundle(dir.path());
        assert_eq!(report.total_files, 3);
        assert_eq!(report.valid_files, 2);
        assert_eq!(report.missing_frontmatter.len(), 1);
        assert_eq!(report.broken_links.len(), 1);
    }

    #[test]
    fn test_attested_computation_execution() {
        let dir = TempDir::new();
        let comp_md = dir.path().join("cpu_count.md");
        let content = r#"---
title: "Compute Core Count"
type: "Attested Computation"
runtime: "bash"
parameters:
  multiplier:
    pattern: "^[0-9]+$"
---
# Computation
```bash
echo $(( 2 * {{multiplier}} ))
```
"#;
        fs::write(&comp_md, content).unwrap();

        let mut params = HashMap::new();
        params.insert("multiplier".to_string(), "4".to_string());

        let receipt = execute_attestation(dir.path(), "cpu_count.md", &params).unwrap();
        assert_eq!(receipt.exit_code, 0);
        assert_eq!(receipt.stdout, "8");
        assert_eq!(receipt.verified, true);
        assert!(!receipt.stdout_sha256.is_empty());
        assert!(!receipt.computation_sha256.is_empty());
    }
}
