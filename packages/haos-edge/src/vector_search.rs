//! Motor acelerado de Busca Vetorial e Híbrida (SIMD/AVX-friendly) em Rust.
//!
//! Lê os vetores armazenados em `memory_vectors` (ou via mmap/SQLite) e calcula a similaridade
//! de cosseno em loop nativo unrolled com autovetorização, atingindo sub-milissegundos por consulta.

use rusqlite::{types::Value, Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap};
use std::path::Path;

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct VectorSearchResult {
    pub record_id: String,
    pub score: f32,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct ParentChildSearchResult {
    pub record_id: String,
    pub parent_id: Option<String>,
    pub score: f32,
    pub confidence: f32,
    pub text_snippet: Option<String>,
    pub parent_content: Option<String>,
}

#[derive(PartialEq)]
struct ScoredItem {
    score: f32,
    record_id: String,
}

#[derive(PartialEq)]
struct ScoredParentChildItem {
    score: f32,
    confidence: f32,
    record_id: String,
    parent_id: Option<String>,
    text_snippet: Option<String>,
    parent_content: Option<String>,
}

impl Eq for ScoredItem {}

impl PartialOrd for ScoredItem {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        // Min-heap ordenado pelo menor score para manter o top-k com O(N log K)
        other.score.partial_cmp(&self.score)
    }
}

impl Ord for ScoredItem {
    fn cmp(&self, other: &Self) -> Ordering {
        self.partial_cmp(other).unwrap_or(Ordering::Equal)
    }
}

impl Eq for ScoredParentChildItem {}

impl PartialOrd for ScoredParentChildItem {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        other.score.partial_cmp(&self.score)
    }
}

impl Ord for ScoredParentChildItem {
    fn cmp(&self, other: &Self) -> Ordering {
        self.partial_cmp(other).unwrap_or(Ordering::Equal)
    }
}

/// Produto escalar e cálculo de cosseno com loop unrolled (SIMD autovetorizado pelo LLVM)
#[inline(always)]
pub fn dot_product_and_norm(a: &[f32], b: &[f32]) -> (f32, f32) {
    let mut dot = 0.0f32;
    let mut norm_b_sq = 0.0f32;

    let len = a.len();
    let chunks = len / 8;
    let remainder = len % 8;

    for i in 0..chunks {
        let offset = i * 8;
        dot += a[offset] * b[offset]
            + a[offset + 1] * b[offset + 1]
            + a[offset + 2] * b[offset + 2]
            + a[offset + 3] * b[offset + 3]
            + a[offset + 4] * b[offset + 4]
            + a[offset + 5] * b[offset + 5]
            + a[offset + 6] * b[offset + 6]
            + a[offset + 7] * b[offset + 7];

        norm_b_sq += b[offset] * b[offset]
            + b[offset + 1] * b[offset + 1]
            + b[offset + 2] * b[offset + 2]
            + b[offset + 3] * b[offset + 3]
            + b[offset + 4] * b[offset + 4]
            + b[offset + 5] * b[offset + 5]
            + b[offset + 6] * b[offset + 6]
            + b[offset + 7] * b[offset + 7];
    }

    let rem_offset = chunks * 8;
    for i in 0..remainder {
        let idx = rem_offset + i;
        dot += a[idx] * b[idx];
        norm_b_sq += b[idx] * b[idx];
    }

    (dot, norm_b_sq.sqrt())
}

pub struct NativeVectorEngine;

fn decode_vector(raw: &Value, dimensions: usize) -> Option<Vec<f32>> {
    let vector: Vec<f32> = match raw {
        Value::Text(text) => serde_json::from_str(text).ok()?,
        Value::Blob(bytes) if bytes.len() == dimensions * std::mem::size_of::<f32>() => bytes
            .chunks_exact(4)
            .map(|chunk| f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]))
            .collect(),
        _ => return None,
    };
    (vector.len() == dimensions && vector.iter().all(|v| v.is_finite())).then_some(vector)
}

impl NativeVectorEngine {
    /// Executa busca vetorial direta no arquivo SQLite `vectors.db` usando Rust compilado
    pub fn search_vectors(
        db_path: &Path,
        query_vector: &[f32],
        model_version: &str,
        limit: usize,
    ) -> Result<Vec<VectorSearchResult>, String> {
        if query_vector.is_empty()
            || limit == 0
            || query_vector.iter().any(|v| !v.is_finite())
        {
            return Ok(Vec::new());
        }

        if !db_path.exists() {
            return Err(format!(
                "Vectors database not found at {}",
                db_path.display()
            ));
        }

        // Calcula a norma da query uma única vez
        let mut qnorm_sq = 0.0f32;
        for &v in query_vector {
            qnorm_sq += v * v;
        }
        let qnorm = qnorm_sq.sqrt();
        if qnorm <= f32::EPSILON {
            return Ok(Vec::new());
        }

        let conn = Connection::open_with_flags(
            db_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|e| format!("Failed to open vectors DB: {e}"))?;

        let mut stmt = conn
            .prepare(
                "SELECT record_id, dimensions, vector_json FROM memory_vectors WHERE model_version = ?;",
            )
            .map_err(|e| format!("Failed to prepare vector statement: {e}"))?;

        let mut heap: BinaryHeap<ScoredItem> = BinaryHeap::with_capacity(limit + 1);

        let query_dim = query_vector.len();
        let rows = stmt
            .query_map([model_version], |row| {
                let record_id: String = row.get(0)?;
                let dimensions: usize = row.get(1)?;
                let raw_json: String = row.get(2)?;
                Ok((record_id, dimensions, raw_json))
            })
            .map_err(|e| format!("Query failed: {e}"))?;

        for r in rows.flatten() {
            let (record_id, dimensions, raw_json) = r;
            if dimensions != query_dim {
                continue;
            }

            // Deserializa vetor nativo
            if let Ok(vec) = serde_json::from_str::<Vec<f32>>(&raw_json) {
                if vec.len() != query_dim {
                    continue;
                }

                let (dot, bnorm) = dot_product_and_norm(query_vector, &vec);
                if bnorm > f32::EPSILON {
                    let score = dot / (qnorm * bnorm);
                    if heap.len() < limit {
                        heap.push(ScoredItem { score, record_id });
                    } else if let Some(top) = heap.peek() {
                        if score > top.score {
                            heap.pop();
                            heap.push(ScoredItem { score, record_id });
                        }
                    }
                }
            }
        }

        let mut results = Vec::with_capacity(heap.len());
        while let Some(item) = heap.pop() {
            results.push(VectorSearchResult {
                record_id: item.record_id,
                score: item.score,
            });
        }
        // Reverte para ordem descendente (maior score primeiro)
        results.reverse();
        Ok(results)
    }

    /// Executa busca vetorial hierárquica Parent-Child ponderada por Confiança
    /// Busca no vetor do Child (parágrafo/fato atômico), mas retorna também o Parent (seção/documento)
    /// Aplica re-ranking por confiança: score_final = score_cosseno * (0.5 + 0.5 * confidence)
    pub fn search_parent_child_vectors(
        db_path: &Path,
        query_vector: &[f32],
        model_version: &str,
        limit: usize,
        min_confidence: f32,
        deduplicate_by_parent: bool,
    ) -> Result<Vec<ParentChildSearchResult>, String> {
        if query_vector.is_empty()
            || limit == 0
            || query_vector.iter().any(|v| !v.is_finite())
            || !min_confidence.is_finite()
        {
            return Ok(Vec::new());
        }

        if !db_path.exists() {
            return Err(format!(
                "Vectors database not found at {}",
                db_path.display()
            ));
        }

        let mut qnorm_sq = 0.0f32;
        for &v in query_vector {
            qnorm_sq += v * v;
        }
        let qnorm = qnorm_sq.sqrt();
        if qnorm <= f32::EPSILON {
            return Ok(Vec::new());
        }

        let conn = Connection::open_with_flags(
            db_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|e| format!("Failed to open vectors DB: {e}"))?;

        // Tenta query com colunas parent-child; se não existirem, faz fallback gracioso
        let has_pc_columns = conn
            .prepare("SELECT parent_id, confidence, text_snippet, parent_content FROM memory_vectors LIMIT 0;")
            .is_ok();

        let query_dim = query_vector.len();
        let mut candidates: Vec<ScoredParentChildItem> = Vec::new();

        if has_pc_columns {
            let mut stmt = conn
                .prepare(
                    "SELECT record_id, dimensions, vector_json, parent_id, confidence, text_snippet, parent_content
                     FROM memory_vectors WHERE model_version = ?;",
                )
                .map_err(|e| format!("Failed to prepare vector statement: {e}"))?;

            let rows = stmt
                .query_map([model_version], |row| {
                    let record_id: String = row.get(0)?;
                    let dimensions: usize = row.get(1)?;
                    let raw_value: Value = row.get(2)?;
                    let parent_id: Option<String> = row.get(3)?;
                    let confidence: f32 = row.get::<_, Option<f64>>(4)?.map(|c| c as f32).unwrap_or(1.0);
                    let text_snippet: Option<String> = row.get(5)?;
                    let parent_content: Option<String> = row.get(6)?;
                    Ok((record_id, dimensions, raw_value, parent_id, confidence, text_snippet, parent_content))
                })
                .map_err(|e| format!("Query failed: {e}"))?;

            for r in rows.flatten() {
                let (record_id, dimensions, raw_value, parent_id, confidence, text_snippet, parent_content) = r;
                if dimensions != query_dim || confidence < min_confidence {
                    continue;
                }

                if let Some(vec) = decode_vector(&raw_value, query_dim) {
                    let (dot, bnorm) = dot_product_and_norm(query_vector, &vec);
                    let score = dot / (qnorm * bnorm);
                    if bnorm > f32::EPSILON && score.is_finite() {
                        let score = score * (0.5 + 0.5 * confidence.clamp(0.0, 1.0));
                        candidates.push(ScoredParentChildItem {
                            score,
                            confidence,
                            record_id,
                            parent_id,
                            text_snippet,
                            parent_content,
                        });
                    }
                }
            }
        } else {
            let mut stmt = conn
                .prepare(
                    "SELECT record_id, dimensions, vector_json FROM memory_vectors WHERE model_version = ?;",
                )
                .map_err(|e| format!("Failed to prepare fallback statement: {e}"))?;

            let rows = stmt
                .query_map([model_version], |row| {
                    let record_id: String = row.get(0)?;
                    let dimensions: usize = row.get(1)?;
                    let raw_value: Value = row.get(2)?;
                    Ok((record_id, dimensions, raw_value))
                })
                .map_err(|e| format!("Fallback query failed: {e}"))?;

            for r in rows.flatten() {
                let (record_id, dimensions, raw_value) = r;
                if dimensions != query_dim {
                    continue;
                }

                if let Some(vec) = decode_vector(&raw_value, query_dim) {
                    let (dot, bnorm) = dot_product_and_norm(query_vector, &vec);
                    if bnorm > f32::EPSILON {
                        let score = dot / (qnorm * bnorm);
                        candidates.push(ScoredParentChildItem {
                            score,
                            confidence: 1.0,
                            record_id,
                            parent_id: None,
                            text_snippet: None,
                            parent_content: None,
                        });
                    }
                }
            }
        }

        // Ordena por score descendente
        candidates.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(Ordering::Equal));

        if deduplicate_by_parent {
            let mut seen_parents: HashMap<String, bool> = HashMap::new();
            let mut deduped = Vec::with_capacity(limit);
            for item in candidates {
                let group_key = item.parent_id.clone().unwrap_or_else(|| item.record_id.clone());
                if seen_parents.contains_key(&group_key) {
                    continue;
                }
                seen_parents.insert(group_key, true);
                deduped.push(ParentChildSearchResult {
                    record_id: item.record_id,
                    parent_id: item.parent_id,
                    score: item.score,
                    confidence: item.confidence,
                    text_snippet: item.text_snippet,
                    parent_content: item.parent_content,
                });
                if deduped.len() >= limit {
                    break;
                }
            }
            Ok(deduped)
        } else {
            let results: Vec<ParentChildSearchResult> = candidates
                .into_iter()
                .take(limit)
                .map(|item| ParentChildSearchResult {
                    record_id: item.record_id,
                    parent_id: item.parent_id,
                    score: item.score,
                    confidence: item.confidence,
                    text_snippet: item.text_snippet,
                    parent_content: item.parent_content,
                })
                .collect();
            Ok(results)
        }
    }

    /// Upsert direto e thread-safe de vetor no SQLite com WAL e busy_timeout (Zero-GIL)
    pub fn upsert_vector(
        db_path: &Path,
        record_id: &str,
        model_version: &str,
        vector: &[f32],
    ) -> Result<(), String> {
        Self::upsert_parent_child_vector(
            db_path,
            record_id,
            model_version,
            vector,
            None,
            1.0,
            None,
            None,
        )
    }

    /// Upsert hierárquico com suporte nativo a Parent-Child e Score de Confiança
    pub fn upsert_parent_child_vector(
        db_path: &Path,
        record_id: &str,
        model_version: &str,
        vector: &[f32],
        parent_id: Option<&str>,
        confidence: f32,
        text_snippet: Option<&str>,
        parent_content: Option<&str>,
    ) -> Result<(), String> {
        if record_id.is_empty() {
            return Err("record_id must not be empty".to_string());
        }
        if vector.is_empty() {
            return Err("vector must not be empty".to_string());
        }

        if let Some(parent) = db_path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }

        let conn = Connection::open(db_path)
            .map_err(|e| format!("Failed to open/create vectors DB: {e}"))?;

        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA busy_timeout=30000;
             CREATE TABLE IF NOT EXISTS memory_vectors (
                 record_id TEXT NOT NULL,
                 model_version TEXT NOT NULL,
                 dimensions INTEGER NOT NULL,
                 vector_json TEXT NOT NULL,
                 parent_id TEXT,
                 confidence REAL NOT NULL DEFAULT 1.0,
                 text_snippet TEXT,
                 parent_content TEXT,
                 updated_at REAL NOT NULL DEFAULT 0,
                 PRIMARY KEY(record_id, model_version)
             );
             CREATE TABLE IF NOT EXISTS memory_vector_models (
                 model_version TEXT PRIMARY KEY,
                 dimensions INTEGER NOT NULL,
                 normalize INTEGER NOT NULL,
                 reindex_policy TEXT NOT NULL,
                 updated_at REAL NOT NULL
             );",
        )
        .map_err(|e| format!("Failed to init vector schema: {e}"))?;

        // Garante adição de colunas dinâmicas se a tabela já existia na versão anterior
        let _ = conn.execute("ALTER TABLE memory_vectors ADD COLUMN parent_id TEXT;", []);
        let _ = conn.execute("ALTER TABLE memory_vectors ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0;", []);
        let _ = conn.execute("ALTER TABLE memory_vectors ADD COLUMN text_snippet TEXT;", []);
        let _ = conn.execute("ALTER TABLE memory_vectors ADD COLUMN parent_content TEXT;", []);

        let raw_json = serde_json::to_string(vector)
            .map_err(|e| format!("Failed to serialize vector to json: {e}"))?;

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();

        conn.execute(
            "INSERT OR REPLACE INTO memory_vectors (record_id, model_version, dimensions, vector_json, parent_id, confidence, text_snippet, parent_content, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
            rusqlite::params![
                record_id,
                model_version,
                vector.len(),
                raw_json,
                parent_id,
                confidence as f64,
                text_snippet,
                parent_content,
                now
            ],
        )
        .map_err(|e| format!("Upsert failed: {e}"))?;

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_dot_product_and_norm() {
        let a = vec![1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0];
        let b = vec![1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0];
        let (dot, norm) = dot_product_and_norm(&a, &b);
        assert!((dot - 285.0).abs() < 1e-4);
        assert!((norm - (285.0f32).sqrt()).abs() < 1e-4);
    }

    #[test]
    fn test_benchmark_native_dot_product() {
        let dim = 384;
        let n = 5000;
        let q = vec![0.05f32; dim];
        let v = vec![0.02f32; dim];
        let start = std::time::Instant::now();
        let mut sum = 0.0f32;
        for _ in 0..n {
            let (dot, _) = dot_product_and_norm(&q, &v);
            sum += dot;
        }
        let elapsed = start.elapsed();
        println!(
            "RUST NATIVO (5.000 vetores x 384d): {:.3} ms (checksum: {})",
            elapsed.as_secs_f64() * 1000.0,
            sum
        );
    }

    #[test]
    fn test_parent_child_search_and_confidence() {
        let temp_dir = tempfile::tempdir().unwrap();
        let db_path = temp_dir.path().join("test_vectors.db");

        // Insere 2 filhos do mesmo pai e 1 filho de outro pai
        // Child 1 (Doc A, Section 1): alta confiança (0.95), vetor alinhado
        NativeVectorEngine::upsert_parent_child_vector(
            &db_path,
            "child_1",
            "model_v1",
            &[1.0, 0.0, 0.0, 0.0],
            Some("doc_a"),
            0.95,
            Some("Parágrafo sobre portas de rede"),
            Some("Documento A completo sobre Infraestrutura"),
        ).unwrap();

        // Child 2 (Doc A, Section 2): média confiança (0.80), vetor parcialmente alinhado
        NativeVectorEngine::upsert_parent_child_vector(
            &db_path,
            "child_2",
            "model_v1",
            &[0.8, 0.2, 0.0, 0.0],
            Some("doc_a"),
            0.80,
            Some("Parágrafo sobre proxy socks5"),
            Some("Documento A completo sobre Infraestrutura"),
        ).unwrap();

        // Child 3 (Doc B): baixa confiança (0.30) com vetor alinhado
        NativeVectorEngine::upsert_parent_child_vector(
            &db_path,
            "child_3",
            "model_v1",
            &[1.0, 0.0, 0.0, 0.0],
            Some("doc_b"),
            0.30,
            Some("Boato sobre portas"),
            Some("Documento B não verificado"),
        ).unwrap();

        let query = vec![1.0, 0.0, 0.0, 0.0];

        // 1. Busca sem filtro com deduplicação por pai:
        // doc_a deve vir primeiro pois child_1 tem 0.95 de confiança vs child_3 com 0.30
        let res = NativeVectorEngine::search_parent_child_vectors(
            &db_path,
            &query,
            "model_v1",
            10,
            0.0,
            true, // deduplicate by parent
        ).unwrap();

        assert_eq!(res.len(), 2);
        assert_eq!(res[0].parent_id.as_deref(), Some("doc_a"));
        assert_eq!(res[0].record_id, "child_1");
        assert!(res[0].score > res[1].score);
        assert_eq!(res[0].parent_content.as_deref(), Some("Documento A completo sobre Infraestrutura"));

        // 2. Busca filtrando confiança mínima >= 0.7:
        // doc_b (child_3) deve ser descartado
        let res_filtered = NativeVectorEngine::search_parent_child_vectors(
            &db_path,
            &query,
            "model_v1",
            10,
            0.7,
            false,
        ).unwrap();

        assert_eq!(res_filtered.len(), 2);
        assert!(res_filtered.iter().all(|r| r.confidence >= 0.7));
    }
}
