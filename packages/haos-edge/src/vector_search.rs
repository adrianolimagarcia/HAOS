//! Motor acelerado de Busca Vetorial e Híbrida (SIMD/AVX-friendly) em Rust.
//!
//! Lê os vetores armazenados em `memory_vectors` (ou via mmap/SQLite) e calcula a similaridade
//! de cosseno em loop nativo unrolled com autovetorização, atingindo sub-milissegundos por consulta.

use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::collections::BinaryHeap;
use std::path::Path;

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct VectorSearchResult {
    pub record_id: String,
    pub score: f32,
}

#[derive(PartialEq)]
struct ScoredItem {
    score: f32,
    record_id: String,
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

impl NativeVectorEngine {
    /// Executa busca vetorial direta no arquivo SQLite `vectors.db` usando Rust compilado
    pub fn search_vectors(
        db_path: &Path,
        query_vector: &[f32],
        model_version: &str,
        limit: usize,
    ) -> Result<Vec<VectorSearchResult>, String> {
        if query_vector.is_empty() {
            return Ok(Vec::new());
        }

        if !db_path.exists() {
            return Err(format!("Vectors database not found at {}", db_path.display()));
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

    /// Upsert direto e thread-safe de vetor no SQLite com WAL e busy_timeout (Zero-GIL)
    pub fn upsert_vector(
        db_path: &Path,
        record_id: &str,
        model_version: &str,
        vector: &[f32],
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
                 updated_at REAL NOT NULL DEFAULT 0,
                 PRIMARY KEY(record_id, model_version)
             );
             CREATE TABLE IF NOT EXISTS memory_vector_models (
                 model_version TEXT PRIMARY KEY,
                 dimensions INTEGER NOT NULL,
                 normalize INTEGER NOT NULL,
                 reindex_policy TEXT NOT NULL,
                 updated_at REAL NOT NULL
             );"
        )
        .map_err(|e| format!("Failed to init vector schema: {e}"))?;

        let raw_json = serde_json::to_string(vector)
            .map_err(|e| format!("Failed to serialize vector to json: {e}"))?;

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();

        conn.execute(
            "INSERT OR REPLACE INTO memory_vectors (record_id, model_version, dimensions, vector_json, updated_at)
             VALUES (?, ?, ?, ?, ?);",
            rusqlite::params![record_id, model_version, vector.len(), raw_json, now],
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
        println!("RUST NATIVO (5.000 vetores x 384d): {:.3} ms (checksum: {})", elapsed.as_secs_f64() * 1000.0, sum);
    }

}
