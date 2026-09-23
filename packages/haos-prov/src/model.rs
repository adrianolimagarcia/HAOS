use serde::{Deserialize, Serialize};
use serde_yaml::Value;

fn default_vec() -> Vec<String> {
    Vec::new()
}

/// The structured PROV-O subset carried by an ADR frontmatter.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProvBlock {
    #[serde(rename = "wasGeneratedBy", default)]
    pub was_generated_by: Option<String>,
    #[serde(rename = "wasAssociatedWith", default)]
    pub was_associated_with: Option<String>,
    #[serde(rename = "wasDerivedFrom", default, deserialize_with = "string_or_vec")]
    pub was_derived_from: Vec<String>,
    #[serde(default)]
    pub causado_by: Option<String>,
    #[serde(default, deserialize_with = "string_or_vec")]
    pub affects: Vec<String>,
    #[serde(default, deserialize_with = "string_or_vec")]
    pub supersedes: Vec<String>,
    #[serde(default)]
    pub superseded_by: Option<String>,
}

/// YAML metadata accepted from an ADR's frontmatter.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct AdrFrontmatter {
    pub id: String,
    #[serde(alias = "title", default)]
    pub titulo: String,
    #[serde(default)]
    pub status: String,
    #[serde(default, deserialize_with = "string_or_vec")]
    pub causado_by: Vec<String>,
    #[serde(default, deserialize_with = "string_or_vec")]
    pub evidence: Vec<String>,
    #[serde(default, deserialize_with = "string_or_vec")]
    pub affects: Vec<String>,
    #[serde(default, deserialize_with = "string_or_vec")]
    pub supersedes: Vec<String>,
    #[serde(default)]
    pub superseded_by: Option<String>,
    #[serde(default)]
    pub prov: ProvBlock,
    /// Other frontmatter values are intentionally not used by the graph.
    #[serde(default)]
    pub data: Option<Value>,
}

/// A parsed causal node, retaining its source filename for deterministic output.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ProvNode {
    pub id: String,
    pub titulo: String,
    pub status: String,
    pub file: String,
    pub causado_by: Vec<String>,
    pub evidence: Vec<String>,
    pub affects: Vec<String>,
    pub supersedes: Vec<String>,
    pub superseded_by: Option<String>,
    pub prov: ProvBlock,
}

impl ProvNode {
    pub(crate) fn from_frontmatter(frontmatter: AdrFrontmatter, file: String) -> Self {
        Self {
            id: normalize_adr_id(&frontmatter.id),
            titulo: frontmatter.titulo,
            status: frontmatter.status,
            file,
            causado_by: frontmatter.causado_by,
            evidence: frontmatter.evidence,
            affects: frontmatter.affects,
            supersedes: frontmatter.supersedes,
            superseded_by: frontmatter.superseded_by,
            prov: frontmatter.prov,
        }
    }
}

/// Normalize `adr:ADR-1`, `ADR-1`, and `1` to the canonical ADR identifier.
pub fn normalize_adr_id(raw: &str) -> String {
    let mut value = raw.trim();
    if value.len() >= 4 && value[..4].eq_ignore_ascii_case("adr:") {
        value = value[4..].trim();
    }
    let lower = value.to_ascii_lowercase();
    let digits = lower.strip_prefix("adr-").or_else(|| {
        if lower.chars().all(|c| c.is_ascii_digit()) {
            Some(lower.as_str())
        } else {
            None
        }
    });
    match digits {
        Some(digits) if !digits.is_empty() && digits.chars().all(|c| c.is_ascii_digit()) => {
            match digits.parse::<u64>() {
                Ok(number) => format!("ADR-{number:03}"),
                Err(_) => value.to_ascii_uppercase(),
            }
        }
        _ => value.to_ascii_uppercase(),
    }
}

fn string_or_vec<'de, D>(deserializer: D) -> Result<Vec<String>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum StringOrVec {
        String(String),
        Vec(Vec<String>),
    }
    match Option::<StringOrVec>::deserialize(deserializer)? {
        None => Ok(default_vec()),
        Some(StringOrVec::String(value)) => {
            if value.trim().is_empty() {
                Ok(default_vec())
            } else {
                Ok(vec![value.trim().to_owned()])
            }
        }
        Some(StringOrVec::Vec(values)) => Ok(values
            .into_iter()
            .filter(|value| !value.trim().is_empty())
            .map(|value| value.trim().to_owned())
            .collect()),
    }
}
