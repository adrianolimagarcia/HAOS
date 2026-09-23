use std::collections::BTreeMap;
use std::fmt;

use thiserror::Error;

use crate::model::{AdrFrontmatter, ProvNode};

const DELIMITER: &str = "---";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParsedDocument {
    pub frontmatter: AdrFrontmatter,
    pub body: String,
}

#[derive(Debug, Error)]
pub enum ParseError {
    #[error("frontmatter must start with a line containing only ---")]
    MissingOpeningDelimiter,
    #[error("frontmatter is missing its closing --- delimiter")]
    MissingClosingDelimiter,
    #[error("frontmatter YAML must be a mapping")]
    NotAMapping,
    #[error("invalid frontmatter YAML: {0}")]
    InvalidYaml(String),
}

impl PartialEq for ParseError {
    fn eq(&self, other: &Self) -> bool {
        std::mem::discriminant(self) == std::mem::discriminant(other)
    }
}
impl Eq for ParseError {}

impl From<serde_yaml::Error> for ParseError {
    fn from(error: serde_yaml::Error) -> Self {
        Self::InvalidYaml(error.to_string())
    }
}

/// Parse only YAML between exact line delimiters; the Markdown body is never YAML input.
pub fn parse_frontmatter(source: &str) -> Result<ParsedDocument, ParseError> {
    let normalized = source.strip_prefix('\u{feff}').unwrap_or(source);
    let Some(first_newline) = normalized.find('\n') else {
        return Err(ParseError::MissingClosingDelimiter);
    };
    let first = normalized[..first_newline].trim_end_matches('\r');
    if first != DELIMITER {
        return Err(ParseError::MissingOpeningDelimiter);
    }

    let mut yaml = String::new();
    let mut cursor = first_newline + 1;
    let body_start = loop {
        if cursor > normalized.len() {
            return Err(ParseError::MissingClosingDelimiter);
        }
        let (line_end, next_cursor) = match normalized[cursor..].find('\n') {
            Some(relative_end) => {
                let end = cursor + relative_end;
                (end, end + 1)
            }
            None => (normalized.len(), normalized.len()),
        };
        let line = normalized[cursor..line_end].trim_end_matches('\r');
        if line == DELIMITER {
            break next_cursor;
        }
        yaml.push_str(&normalized[cursor..next_cursor]);
        if next_cursor == normalized.len() {
            return Err(ParseError::MissingClosingDelimiter);
        }
        cursor = next_cursor;
    };

    let value: serde_yaml::Value = serde_yaml::from_str(&yaml)?;
    if !value.is_mapping() {
        return Err(ParseError::NotAMapping);
    }
    let frontmatter: AdrFrontmatter = serde_yaml::from_value(value)?;
    let body = normalized[body_start..].to_owned();
    Ok(ParsedDocument { frontmatter, body })
}

/// Parse a deterministic map of filename to Markdown documents into a vault.
pub fn parse_vault<I, K, V>(documents: I) -> Result<crate::Vault, Vec<(String, ParseError)>>
where
    I: IntoIterator<Item = (K, V)>,
    K: Into<String>,
    V: AsRef<str>,
{
    let mut nodes = BTreeMap::new();
    let mut errors = Vec::new();
    for (file, source) in documents {
        let file = file.into();
        match parse_frontmatter(source.as_ref()) {
            Ok(document) => {
                let node = ProvNode::from_frontmatter(document.frontmatter, file.clone());
                nodes.insert(node.id.clone(), node);
            }
            Err(error) => errors.push((file, error)),
        }
    }
    if errors.is_empty() {
        Ok(crate::Vault::from_nodes(nodes))
    } else {
        Err(errors)
    }
}

impl fmt::Display for ParsedDocument {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}", self.body)
    }
}
