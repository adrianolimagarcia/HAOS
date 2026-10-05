//! Suporte a Modelos Mentais Compilados via AST (ADR-022).
//!
//! Permite representação estruturada de crenças e diretrizes em Seções e Blocos,
//! mutações atômicas via Delta Operations (AddSection, AppendBlock, ReplaceBlock, RemoveBlock)
//! e compilação em Markdown para lookup O(1) no prompt em runtime (<0.1ms).

use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct MentalModelBlock {
    pub block_id: String,
    pub content: String,
    #[serde(default = "default_proof_count")]
    pub proof_count: u32,
    #[serde(default)]
    pub source_node_ids: Vec<String>,
}

fn default_proof_count() -> u32 {
    1
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct MentalModelSection {
    pub section_id: String,
    pub title: String,
    #[serde(default)]
    pub order: u32,
    #[serde(default)]
    pub blocks: Vec<MentalModelBlock>,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct StructuredDocument {
    pub model_id: String,
    pub title: String,
    #[serde(default = "default_version")]
    pub version: u64,
    #[serde(default)]
    pub sections: Vec<MentalModelSection>,
}

fn default_version() -> u64 {
    1
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
#[serde(tag = "type")]
pub enum DeltaOp {
    #[serde(rename = "add_section")]
    AddSection {
        section_id: String,
        title: String,
        #[serde(default)]
        order: u32,
    },
    #[serde(rename = "append_block")]
    AppendBlock {
        section_id: String,
        block: MentalModelBlock,
    },
    #[serde(rename = "replace_block")]
    ReplaceBlock {
        section_id: String,
        block_id: String,
        new_content: String,
        #[serde(default)]
        proof_increment: u32,
    },
    #[serde(rename = "remove_block")]
    RemoveBlock {
        section_id: String,
        block_id: String,
    },
}

impl StructuredDocument {
    pub fn new(model_id: impl Into<String>, title: impl Into<String>) -> Self {
        Self {
            model_id: model_id.into(),
            title: title.into(),
            version: 1,
            sections: Vec::new(),
        }
    }

    /// Compila o AST diretamente em Markdown otimizado para o System Prompt.
    pub fn compile_to_markdown(&self) -> String {
        let mut out = String::with_capacity(1024);
        out.push_str(&format!("# {}\n\n", self.title));

        let mut sorted_sections = self.sections.clone();
        sorted_sections.sort_by_key(|s| s.order);

        for sec in sorted_sections {
            out.push_str(&format!("## {}\n", sec.title));
            for b in sec.blocks {
                out.push_str(&format!("- {} (provas: {})\n", b.content, b.proof_count));
            }
            out.push('\n');
        }
        out.trim().to_string()
    }

    /// Aplica uma mutação DeltaOp atômica incrementando a versão do documento.
    pub fn apply_delta(&mut self, op: &DeltaOp) -> Result<(), String> {
        match op {
            DeltaOp::AddSection {
                section_id,
                title,
                order,
            } => {
                if let Some(sec) = self.sections.iter_mut().find(|s| &s.section_id == section_id) {
                    sec.title = title.clone();
                    sec.order = *order;
                } else {
                    self.sections.push(MentalModelSection {
                        section_id: section_id.clone(),
                        title: title.clone(),
                        order: *order,
                        blocks: Vec::new(),
                    });
                }
            }
            DeltaOp::AppendBlock { section_id, block } => {
                let sec = self
                    .sections
                    .iter_mut()
                    .find(|s| &s.section_id == section_id)
                    .ok_or_else(|| format!("Section '{section_id}' not found"))?;
                if let Some(b) = sec.blocks.iter_mut().find(|b| b.block_id == block.block_id) {
                    b.content = block.content.clone();
                    b.proof_count = block.proof_count;
                    b.source_node_ids = block.source_node_ids.clone();
                } else {
                    sec.blocks.push(block.clone());
                }
            }
            DeltaOp::ReplaceBlock {
                section_id,
                block_id,
                new_content,
                proof_increment,
            } => {
                let sec = self
                    .sections
                    .iter_mut()
                    .find(|s| &s.section_id == section_id)
                    .ok_or_else(|| format!("Section '{section_id}' not found"))?;
                let blk = sec
                    .blocks
                    .iter_mut()
                    .find(|b| &b.block_id == block_id)
                    .ok_or_else(|| format!("Block '{block_id}' not found in section '{section_id}'"))?;
                blk.content = new_content.clone();
                blk.proof_count += proof_increment;
            }
            DeltaOp::RemoveBlock {
                section_id,
                block_id,
            } => {
                let sec = self
                    .sections
                    .iter_mut()
                    .find(|s| &s.section_id == section_id)
                    .ok_or_else(|| format!("Section '{section_id}' not found"))?;
                let before_len = sec.blocks.len();
                sec.blocks.retain(|b| &b.block_id != block_id);
                if sec.blocks.len() == before_len {
                    return Err(format!("Block '{block_id}' not found in section '{section_id}'"));
                }
            }
        }
        self.version += 1;
        Ok(())
    }

    /// Persiste o documento e o markdown compilado em `haos_mental_models`.
    pub fn save_to_db(&self, conn: &Connection) -> Result<(), String> {
        let ast_json = serde_json::to_string(self)
            .map_err(|e| format!("Failed to serialize AST to JSON: {e}"))?;
        let compiled_md = self.compile_to_markdown();
        let token_count = (compiled_md.len() / 4) as i64;
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0) as i64;

        conn.execute(
            "INSERT INTO haos_mental_models (
                model_id, title, version, ast_json, compiled_markdown, token_count, last_refreshed_at, last_memory_write_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
             ON CONFLICT(model_id) DO UPDATE SET
                title = excluded.title,
                version = excluded.version,
                ast_json = excluded.ast_json,
                compiled_markdown = excluded.compiled_markdown,
                token_count = excluded.token_count,
                last_refreshed_at = excluded.last_refreshed_at,
                last_memory_write_at = excluded.last_memory_write_at;",
            params![
                self.model_id,
                self.title,
                self.version as i64,
                ast_json,
                compiled_md,
                token_count,
                now,
                now,
            ],
        )
        .map_err(|e| format!("Failed to save mental model '{}': {e}", self.model_id))?;

        Ok(())
    }

    /// Carrega o documento estruturado a partir do banco SQLite.
    pub fn load_from_db(conn: &Connection, model_id: &str) -> Result<Option<StructuredDocument>, String> {
        let mut stmt = conn
            .prepare("SELECT ast_json FROM haos_mental_models WHERE model_id = ?1 LIMIT 1;")
            .map_err(|e| format!("Prepare query failed: {e}"))?;

        let mut rows = stmt
            .query([model_id])
            .map_err(|e| format!("Query failed: {e}"))?;

        if let Some(row) = rows.next().map_err(|e| format!("Read row failed: {e}"))? {
            let ast_json: String = row.get(0).map_err(|e| format!("Get ast_json failed: {e}"))?;
            let doc: StructuredDocument = serde_json::from_str(&ast_json)
                .map_err(|e| format!("Failed to parse StructuredDocument JSON: {e}"))?;
            Ok(Some(doc))
        } else {
            Ok(None)
        }
    }

    /// Lookup O(1) do Markdown pré-compilado diretamente em SQLite.
    pub fn get_compiled_markdown(conn: &Connection, model_id: &str) -> Result<Option<String>, String> {
        let mut stmt = conn
            .prepare("SELECT compiled_markdown FROM haos_mental_models WHERE model_id = ?1 LIMIT 1;")
            .map_err(|e| format!("Prepare query failed: {e}"))?;

        let mut rows = stmt
            .query([model_id])
            .map_err(|e| format!("Query failed: {e}"))?;

        if let Some(row) = rows.next().map_err(|e| format!("Read row failed: {e}"))? {
            let md: String = row.get(0).map_err(|e| format!("Get compiled_markdown failed: {e}"))?;
            Ok(Some(md))
        } else {
            Ok(None)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mental_model_ast_lifecycle() {
        let mut doc = StructuredDocument::new("test_model", "Diretrizes de Teste");

        // 1. Add section
        let op1 = DeltaOp::AddSection {
            section_id: "sec1".to_string(),
            title: "Regras Gerais".to_string(),
            order: 1,
        };
        doc.apply_delta(&op1).unwrap();
        assert_eq!(doc.sections.len(), 1);
        assert_eq!(doc.version, 2);

        // 2. Append block
        let b1 = MentalModelBlock {
            block_id: "b1".to_string(),
            content: "Sempre usar Rust em baixo nível".to_string(),
            proof_count: 3,
            source_node_ids: vec!["mem_1".to_string()],
        };
        doc.apply_delta(&DeltaOp::AppendBlock {
            section_id: "sec1".to_string(),
            block: b1,
        }).unwrap();
        assert_eq!(doc.sections[0].blocks.len(), 1);

        // 3. Replace block com incremento de prova
        doc.apply_delta(&DeltaOp::ReplaceBlock {
            section_id: "sec1".to_string(),
            block_id: "b1".to_string(),
            new_content: "Sempre usar Rust e rusqlite".to_string(),
            proof_increment: 2,
        }).unwrap();
        assert_eq!(doc.sections[0].blocks[0].content, "Sempre usar Rust e rusqlite");
        assert_eq!(doc.sections[0].blocks[0].proof_count, 5);

        // 4. Compilação para Markdown
        let md = doc.compile_to_markdown();
        assert!(md.contains("# Diretrizes de Teste"));
        assert!(md.contains("## Regras Gerais"));
        assert!(md.contains("- Sempre usar Rust e rusqlite (provas: 5)"));

        // 5. Testar persistência em SQLite
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE haos_mental_models (
                model_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                ast_json TEXT NOT NULL,
                compiled_markdown TEXT NOT NULL,
                token_count INTEGER NOT NULL,
                last_refreshed_at INTEGER NOT NULL,
                last_memory_write_at INTEGER NOT NULL
            );",
        ).unwrap();

        doc.save_to_db(&conn).unwrap();

        let loaded = StructuredDocument::load_from_db(&conn, "test_model").unwrap().unwrap();
        assert_eq!(loaded.title, "Diretrizes de Teste");
        assert_eq!(loaded.sections[0].blocks[0].proof_count, 5);

        let compiled = StructuredDocument::get_compiled_markdown(&conn, "test_model").unwrap().unwrap();
        assert_eq!(compiled, md);
    }
}
