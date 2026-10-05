//! Motor de Busca e Raciocínio TEMPR (Temporal Episodic Memory with Path Retrieval).
//!
//! Absorve a formulação SOTA do Hindsight (LongMemEval 91.4%):
//! 1. Reciprocal Rank Fusion (RRF) combinando Dense (vetorial SIMD), Sparse (FTS5 BM25) e Grafo Causal (BFS K-hops).
//! 2. Multiplicadores de calibração rigorosos:
//!    - Boost de Recência B_recency
//!    - Boost de Janela Temporal Intervalar B_temporal [valid_from, valid_until]
//!    - Boost de Lastro Probatório B_proof baseado em proof_count empírico.
//!
//! S_final = RRF(R_dense, R_sparse, R_graph) * B_recency * B_temporal * B_proof

use rusqlite::Connection;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct TEMPRParams {
    pub k_rrf: f32,          // Constante RRF (default 60.0)
    pub alpha_recency: f32,  // Ponderação de recência (default 0.3)
    pub alpha_temporal: f32, // Ponderação de overlap temporal (default 0.4)
    pub alpha_proof: f32,    // Ponderação de contagem de provas (default 0.5)
    pub proof_cap: f32,      // Teto de saturação de provas (default 10.0)
}

impl Default for TEMPRParams {
    fn default() -> Self {
        Self {
            k_rrf: 60.0,
            alpha_recency: 0.3,
            alpha_temporal: 0.4,
            alpha_proof: 0.5,
            proof_cap: 10.0,
        }
    }
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct TEMPRCandidate {
    pub node_id: String,
    pub label: String,
    pub content: String,
    pub dense_rank: Option<usize>,
    pub sparse_rank: Option<usize>,
    pub graph_rank: Option<usize>,
    pub recency_score: f32,
    pub temporal_overlap: f32,
    pub proof_count: u32,
    pub final_score: f32,
}

impl TEMPRCandidate {
    pub fn calculate_score(&self, params: &TEMPRParams) -> f32 {
        let mut rrf = 0.0f32;
        if let Some(r) = self.dense_rank {
            rrf += 1.0 / (params.k_rrf + r as f32);
        }
        if let Some(r) = self.sparse_rank {
            rrf += 1.0 / (params.k_rrf + r as f32);
        }
        if let Some(r) = self.graph_rank {
            rrf += 1.0 / (params.k_rrf + r as f32);
        }

        let b_rec = 1.0 + params.alpha_recency * (self.recency_score - 0.5);
        let b_temp = 1.0 + params.alpha_temporal * (self.temporal_overlap - 0.5);
        let norm_proof = (self.proof_count as f32).min(params.proof_cap) / params.proof_cap;
        let b_proof = 1.0 + params.alpha_proof * (norm_proof - 0.5);

        (rrf * b_rec * b_temp * b_proof).max(0.0)
    }
}

pub struct TEMPREngine;

impl TEMPREngine {
    /// Executa busca híbrida unificada TEMPR diretamente em SQLite
    pub fn search(
        conn: &Connection,
        query_text: &str,
        dense_matches: &[(String, f32)], // (record_id/node_id, score) ordenados desc
        time_start: Option<f64>,
        time_end: Option<f64>,
        limit: usize,
        params: &TEMPRParams,
    ) -> Result<Vec<TEMPRCandidate>, String> {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);

        let mut candidate_map: HashMap<String, TEMPRCandidate> = HashMap::new();

        // 1. Processar Dense Ranks
        for (idx, (rec_id, _score)) in dense_matches.iter().enumerate() {
            let node_id = if rec_id.starts_with("memory:") {
                rec_id.clone()
            } else {
                format!("memory:{rec_id}")
            };
            candidate_map.insert(
                node_id.clone(),
                TEMPRCandidate {
                    node_id,
                    label: String::new(),
                    content: String::new(),
                    dense_rank: Some(idx + 1),
                    sparse_rank: None,
                    graph_rank: None,
                    recency_score: 0.5,
                    temporal_overlap: 1.0,
                    proof_count: 1,
                    final_score: 0.0,
                },
            );
        }

        // 2. Processar Sparse FTS5
        let tokens: Vec<&str> = query_text.split_whitespace().collect();
        let fts_query = tokens
            .iter()
            .map(|t| format!("\"{}\"", t.replace('"', "")))
            .collect::<Vec<_>>()
            .join(" OR ");

        if !fts_query.is_empty() {
            let mut stmt = conn.prepare(
                "SELECT node_id, bm25(haos_graph_fts) AS rank
                 FROM haos_graph_fts
                 WHERE haos_graph_fts MATCH ?1
                 ORDER BY rank ASC LIMIT 50;",
            ).map_err(|e| format!("FTS query failed: {e}"))?;

            let fts_rows: Vec<(String, f64)> = {
                let rows_iter = stmt.query_map([&fts_query], |row| {
                    Ok((row.get::<_, String>(0)?, row.get::<_, f64>(1)?))
                }).map_err(|e| format!("Query map failed: {e}"))?;
                rows_iter.flatten().collect()
            };

            for (idx, (node_id, _bm25_rank)) in fts_rows.into_iter().enumerate() {
                let cand = candidate_map.entry(node_id.clone()).or_insert_with(|| {
                    TEMPRCandidate {
                        node_id,
                        label: String::new(),
                        content: String::new(),
                        dense_rank: None,
                        sparse_rank: None,
                        graph_rank: None,
                        recency_score: 0.5,
                        temporal_overlap: 1.0,
                        proof_count: 1,
                        final_score: 0.0,
                    }
                });
                cand.sparse_rank = Some(idx + 1);
            }
        }

        // 3. Processar Graph Walk Ranks para sementes top
        let seed_ids: Vec<String> = candidate_map.keys().cloned().take(10).collect();
        let mut visited_neighbors = HashSet::new();
        let mut graph_rank_counter = 1;

        for s_id in seed_ids {
            let mut e_stmt = conn.prepare(
                "SELECT target_id FROM haos_graph_edges WHERE source_id = ?1
                 UNION
                 SELECT source_id FROM haos_graph_edges WHERE target_id = ?1 LIMIT 10;",
            ).map_err(|e| format!("Edge query failed: {e}"))?;

            let neighbors: Vec<String> = {
                let e_iter = e_stmt.query_map([&s_id], |row| row.get::<_, String>(0))
                    .map_err(|e| format!("Edge query map failed: {e}"))?;
                e_iter.flatten().collect()
            };

            for neighbor in neighbors {
                if visited_neighbors.insert(neighbor.clone()) {
                    let cand = candidate_map.entry(neighbor.clone()).or_insert_with(|| {
                        TEMPRCandidate {
                            node_id: neighbor,
                            label: String::new(),
                            content: String::new(),
                            dense_rank: None,
                            sparse_rank: None,
                            graph_rank: None,
                            recency_score: 0.5,
                            temporal_overlap: 1.0,
                            proof_count: 1,
                            final_score: 0.0,
                        }
                    });
                    if cand.graph_rank.is_none() {
                        cand.graph_rank = Some(graph_rank_counter);
                        graph_rank_counter += 1;
                    }
                }
            }
        }

        // 4. Carregar metadados dos nós e calcular os multiplicadores
        for cand in candidate_map.values_mut() {
            if let Ok(mut n_stmt) = conn.prepare(
                "SELECT label, content, created_at, valid_from, valid_until, proof_count
                 FROM haos_graph_nodes WHERE node_id = ?1 LIMIT 1;",
            ) {
                if let Ok(mut rows) = n_stmt.query([&cand.node_id]) {
                    if let Some(row) = rows.next().unwrap_or(None) {
                        cand.label = row.get::<_, String>(0).unwrap_or_default();
                        cand.content = row.get::<_, String>(1).unwrap_or_default();
                        let created_at: f64 = row.get(2).unwrap_or(now);
                        let valid_from: Option<i64> = row.get(3).unwrap_or(None);
                        let valid_until: Option<i64> = row.get(4).unwrap_or(None);
                        cand.proof_count = row.get::<_, u32>(5).unwrap_or(1);

                        // Cálculo de Recência [0.0, 1.0]
                        let age_days = ((now - created_at).max(0.0) / 86400.0) as f32;
                        cand.recency_score = 1.0 / (1.0 + (age_days / 30.0));

                        // Cálculo de Overlap Temporal [0.0, 1.0]
                        cand.temporal_overlap = match (time_start, time_end, valid_from, valid_until) {
                            (Some(qs), Some(qe), Some(vf), Some(vu)) => {
                                let v_start = vf as f64;
                                let v_end = vu as f64;
                                if qs <= v_end && qe >= v_start {
                                    1.0
                                } else {
                                    0.1 // Fora da janela de validade
                                }
                            }
                            (Some(qs), _, _, Some(vu)) => {
                                if qs > vu as f64 { 0.1 } else { 1.0 }
                            }
                            (_, Some(qe), Some(vf), _) => {
                                if qe < vf as f64 { 0.1 } else { 1.0 }
                            }
                            _ => 1.0,
                        };
                    }
                }
            }

            cand.final_score = cand.calculate_score(params);
        }

        let mut results: Vec<TEMPRCandidate> = candidate_map.into_values().collect();
        results.sort_by(|a, b| b.final_score.partial_cmp(&a.final_score).unwrap_or(std::cmp::Ordering::Equal));
        results.truncate(limit);

        Ok(results)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tempr_scoring_calibration() {
        let params = TEMPRParams::default();

        let cand_high_proof = TEMPRCandidate {
            node_id: "node_1".to_string(),
            label: "Fato Sólido".to_string(),
            content: "Confirmado 10 vezes".to_string(),
            dense_rank: Some(1),
            sparse_rank: Some(2),
            graph_rank: None,
            recency_score: 0.9,
            temporal_overlap: 1.0,
            proof_count: 10,
            final_score: 0.0,
        };

        let cand_unproven = TEMPRCandidate {
            node_id: "node_2".to_string(),
            label: "Fato Frágil".to_string(),
            content: "Sem evidência".to_string(),
            dense_rank: Some(1),
            sparse_rank: Some(2),
            graph_rank: None,
            recency_score: 0.9,
            temporal_overlap: 1.0,
            proof_count: 1,
            final_score: 0.0,
        };

        let score_high = cand_high_proof.calculate_score(&params);
        let score_low = cand_unproven.calculate_score(&params);

        assert!(score_high > score_low, "Nó com 10 provas deve ter score superior ao unproven");
    }
}
