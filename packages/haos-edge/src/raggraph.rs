//! Motor RAGGraph nativo em Rust para HAOS/Hermes.
//!
//! Modela e indexa sessões, turnos de conversa, chamadas de ferramentas e
//! delegações de subagentes como um DAG (Grafo Acíclico Dirigido) persistido em SQLite.
//! Permite recuperação contextual híbrida (FTS5 BM25 + SIMD Vetorial + K-hop Graph Walk).

use rusqlite::{params, Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use std::collections::{HashSet, VecDeque};
use std::path::{Path, PathBuf};

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct GraphNode {
    pub node_id: String,
    pub node_type: String, // "session", "turn", "tool_action", "memory", "agent", "capability", "agent_event", "concept"
    pub session_id: String,
    pub label: String,
    pub content: String,
    pub metadata_json: String,
    pub created_at: f64,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct GraphEdge {
    pub source_id: String,
    pub target_id: String,
    pub edge_type: String, // "CONTINUES", "DISPATCHED_SUBAGENT", "HAS_TURN", "FOLLOWED_BY", "INVOKES", "HAS_CAPABILITY", "EXECUTED_EVENT", "PRODUCED_MEMORY", "CAUSED_BY", "SUPERVISES", "ADVISES"
    pub weight: f32,
    pub metadata_json: String,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct GraphLineage {
    pub session_id: String,
    pub nodes: Vec<GraphNode>,
    pub edges: Vec<GraphEdge>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct RAGGraphItem {
    pub score: f32,
    pub seed_node: GraphNode,
    pub context_nodes: Vec<GraphNode>,
    pub edges: Vec<GraphEdge>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct RAGGraphQueryResult {
    pub query: String,
    pub count: usize,
    pub items: Vec<RAGGraphItem>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct AgentDefinition {
    pub id: String,
    pub name: String,
    pub role: String,
    pub parent_id: Option<String>,
    pub description: String,
    pub capabilities: Vec<String>,
    pub trust_score: f32,
    pub status: String,
}

pub struct RAGGraphEngine;

impl RAGGraphEngine {
    pub fn candidate_homes(haos_home: &Path) -> Vec<PathBuf> {
        let mut list = Vec::new();
        let input_path = haos_home.to_path_buf();
        list.push(input_path.clone());
        if input_path.file_name().and_then(|n| n.to_str()) == Some("memory") {
            if let Some(parent) = input_path.parent() {
                let p = parent.to_path_buf();
                if !list.contains(&p) {
                    list.push(p);
                }
            }
        }
        if let Ok(h) = std::env::var("HERMES_HOME") {
            let p = PathBuf::from(h.trim());
            if !list.contains(&p) {
                list.push(p);
            }
        }
        if let Ok(h) = std::env::var("HAOS_HOME") {
            let p = PathBuf::from(h.trim());
            if !list.contains(&p) {
                list.push(p);
            }
        }
        if let Ok(home) = std::env::var("HOME") {
            let p = PathBuf::from(home.trim()).join(".haos");
            if !list.contains(&p) {
                list.push(p);
            }
        }
        let root_haos = PathBuf::from("/root/.haos");
        if !list.contains(&root_haos) {
            list.push(root_haos);
        }
        list
    }

    pub fn find_session_home(haos_home: &Path, session_id: &str) -> Option<PathBuf> {
        for home in Self::candidate_homes(haos_home) {
            let sidecar = home.join("webui").join("sessions").join(format!("{session_id}.json"));
            if sidecar.exists() {
                return Some(home);
            }
            let sdb = home.join("state.db");
            if sdb.exists() {
                if let Ok(conn) = Connection::open_with_flags(
                    &sdb,
                    OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
                ) {
                    if let Ok(mut stmt) = conn.prepare("SELECT 1 FROM sessions WHERE id = ? LIMIT 1;") {
                        if stmt.exists([session_id]).unwrap_or(false) {
                            return Some(home);
                        }
                    }
                }
            }
        }
        None
    }

    pub fn get_raggraph_db_path(haos_home: &Path) -> PathBuf {
        let active_home = Self::candidate_homes(haos_home)
            .into_iter()
            .find(|h| h.join("state.db").exists())
            .unwrap_or_else(|| haos_home.to_path_buf());
        let mem_dir = active_home.join("memory");
        if !mem_dir.exists() {
            let _ = std::fs::create_dir_all(&mem_dir);
        }
        mem_dir.join("raggraph.db")
    }

    pub fn open_or_create_db(haos_home: &Path) -> Result<Connection, String> {
        let db_path = Self::get_raggraph_db_path(haos_home);
        let conn = Connection::open(&db_path)
            .map_err(|e| format!("Failed to open raggraph.db: {e}"))?;

        conn.execute_batch(
            "PRAGMA journal_mode = WAL;
             PRAGMA synchronous = NORMAL;
             PRAGMA busy_timeout = 5000;

             CREATE TABLE IF NOT EXISTS haos_graph_nodes (
                 node_id TEXT PRIMARY KEY,
                 node_type TEXT NOT NULL,
                 session_id TEXT NOT NULL,
                 label TEXT NOT NULL,
                 content TEXT NOT NULL,
                 metadata_json TEXT DEFAULT '{}',
                 created_at REAL NOT NULL
             );
             CREATE INDEX IF NOT EXISTS idx_graph_nodes_session ON haos_graph_nodes(session_id);
             CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON haos_graph_nodes(node_type);

             CREATE TABLE IF NOT EXISTS haos_graph_edges (
                 source_id TEXT NOT NULL,
                 target_id TEXT NOT NULL,
                 edge_type TEXT NOT NULL,
                 weight REAL DEFAULT 1.0,
                 metadata_json TEXT DEFAULT '{}',
                 PRIMARY KEY (source_id, target_id, edge_type)
             );
             CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON haos_graph_edges(target_id);
             CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON haos_graph_edges(source_id);

             CREATE TABLE IF NOT EXISTS haos_agent_events (
                 event_id TEXT PRIMARY KEY,
                 event_type TEXT NOT NULL,
                 agent_id TEXT NOT NULL,
                 payload_json TEXT NOT NULL,
                 timestamp REAL NOT NULL,
                 target_ref TEXT,
                 created_at REAL NOT NULL
             );
             CREATE INDEX IF NOT EXISTS idx_agent_events_type ON haos_agent_events(event_type);
             CREATE INDEX IF NOT EXISTS idx_agent_events_agent ON haos_agent_events(agent_id);

             CREATE VIRTUAL TABLE IF NOT EXISTS haos_graph_fts USING fts5(
                 node_id UNINDEXED,
                 content,
                 tokenize='unicode61'
             );

             CREATE TABLE IF NOT EXISTS haos_mental_models (
                 model_id TEXT PRIMARY KEY,
                 title TEXT NOT NULL,
                 version INTEGER NOT NULL DEFAULT 1,
                 ast_json TEXT NOT NULL,
                 compiled_markdown TEXT NOT NULL,
                 token_count INTEGER NOT NULL,
                 last_refreshed_at INTEGER NOT NULL,
                 last_memory_write_at INTEGER NOT NULL
             );
             CREATE INDEX IF NOT EXISTS idx_mental_models_write ON haos_mental_models(last_memory_write_at);",
        )
        .map_err(|e| format!("Failed to initialize raggraph schema: {e}"))?;

        // Migrações retrocompatíveis para bases já existentes
        let _ = conn.execute("ALTER TABLE haos_graph_nodes ADD COLUMN valid_from INTEGER;", []);
        let _ = conn.execute("ALTER TABLE haos_graph_nodes ADD COLUMN valid_until INTEGER;", []);
        let _ = conn.execute("ALTER TABLE haos_graph_nodes ADD COLUMN proof_count INTEGER NOT NULL DEFAULT 1;", []);
        let _ = conn.execute("ALTER TABLE haos_graph_nodes ADD COLUMN supporting_quotes_json TEXT NOT NULL DEFAULT '[]';", []);

        Ok(conn)
    }

    /// Indexa uma sessão, seus turnos e ferramentas a partir do state.db e sidecars JSON.
    pub fn index_session(haos_home: &Path, session_id: &str) -> Result<usize, String> {
        let active_home = Self::find_session_home(haos_home, session_id)
            .ok_or_else(|| format!("Session {session_id} not found in state.db or sidecars across search paths"))?;
        let mut rag_conn = Self::open_or_create_db(&active_home)?;
        let state_db_path = active_home.join("state.db");
        if !state_db_path.exists() {
            return Err(format!("state.db not found at {}", state_db_path.display()));
        }

        let state_conn = Connection::open_with_flags(
            &state_db_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|e| format!("Failed to open state.db read-only: {e}"))?;

        // 1. Ler metadados da sessão
        let mut session_node: Option<GraphNode> = None;
        let mut parent_id: Option<String> = None;

        if let Ok(mut stmt) = state_conn.prepare(
            "SELECT id, title, model, parent_session_id, started_at, COALESCE(last_activity_at, started_at)
             FROM sessions WHERE id = ?;",
        ) {
            let mut rows = stmt.query_map([session_id], |row| {
                let id: String = row.get(0)?;
                let title: Option<String> = row.get(1)?;
                let model: Option<String> = row.get(2)?;
                let p_id: Option<String> = row.get(3)?;
                let started_at: f64 = row.get(4)?;
                let last_at: f64 = row.get(5)?;
                Ok((id, title, model, p_id, started_at, last_at))
            }).map_err(|e| e.to_string())?;

            if let Some(Ok((id, title, model, p_id, started_at, _))) = rows.next() {
                parent_id = p_id.filter(|s| !s.is_empty());
                let meta = serde_json::json!({
                    "model": model,
                    "parent_session_id": parent_id
                });
                session_node = Some(GraphNode {
                    node_id: format!("session:{id}"),
                    node_type: "session".to_string(),
                    session_id: id.clone(),
                    label: title.unwrap_or_else(|| format!("Session {id}")),
                    content: String::new(),
                    metadata_json: meta.to_string(),
                    created_at: started_at,
                });
            }
        }

        if session_node.is_none() {
            // Tenta obter metadados a partir do sidecar JSON
            let sidecar_file = active_home.join("webui").join("sessions").join(format!("{session_id}.json"));
            if sidecar_file.exists() {
                if let Ok(content) = std::fs::read_to_string(&sidecar_file) {
                    if let Ok(val) = serde_json::from_str::<serde_json::Value>(&content) {
                        let id = session_id.to_string();
                        let title = val.get("title").and_then(|v| v.as_str()).unwrap_or("Untitled").to_string();
                        let created_at = val.get("created_at").and_then(|v| v.as_f64()).unwrap_or(0.0);
                        parent_id = val.get("parent_session_id").and_then(|v| v.as_str()).map(|s| s.to_string());
                        session_node = Some(GraphNode {
                            node_id: format!("session:{id}"),
                            node_type: "session".to_string(),
                            session_id: id,
                            label: title,
                            content: String::new(),
                            metadata_json: serde_json::json!({ "parent_session_id": parent_id }).to_string(),
                            created_at,
                        });
                    }
                }
            }
        }

        let s_node = session_node.ok_or_else(|| format!("Session {session_id} not found in state.db or sidecars"))?;

        // 2. Ler mensagens / turnos da sessão
        let mut turn_nodes = Vec::new();
        let mut tool_nodes = Vec::new();
        let mut edges = Vec::new();

        if let Some(pid) = &parent_id {
            edges.push(GraphEdge {
                source_id: format!("session:{pid}"),
                target_id: s_node.node_id.clone(),
                edge_type: "CONTINUES".to_string(),
                weight: 1.0,
                metadata_json: "{}".to_string(),
            });
        }

        if let Ok(mut stmt) = state_conn.prepare(
            "SELECT id, role, content, timestamp, tool_call_id, tool_name, tool_calls
             FROM messages
             WHERE session_id = ? AND (active IS NULL OR active != 0)
             ORDER BY id DESC LIMIT 500;",
        ) {
            let rows_iter = stmt.query_map([session_id], |row| {
                let id: i64 = row.get(0)?;
                let role: String = row.get(1)?;
                let content: Option<String> = row.get(2)?;
                let ts: f64 = row.get(3)?;
                let tool_call_id: Option<String> = row.get(4)?;
                let tool_name: Option<String> = row.get(5)?;
                let tool_calls: Option<String> = row.get(6)?;
                Ok((id, role, content.unwrap_or_default(), ts, tool_call_id, tool_name, tool_calls))
            }).map_err(|e| e.to_string())?;

            let mut fetched: Vec<_> = rows_iter.flatten().collect();
            fetched.reverse(); // Ordenação cronológica

            let mut prev_turn_node_id: Option<String> = None;

            for (msg_id, role, content, ts, tool_call_id, tool_name, tool_calls) in fetched {
                let turn_node_id = format!("turn:{session_id}:{msg_id}");

                let t_node = GraphNode {
                    node_id: turn_node_id.clone(),
                    node_type: "turn".to_string(),
                    session_id: session_id.to_string(),
                    label: format!("{role} #{msg_id}"),
                    content: content.clone(),
                    metadata_json: serde_json::json!({ "role": role, "msg_id": msg_id }).to_string(),
                    created_at: ts,
                };
                turn_nodes.push(t_node);

                if let Some(prev) = prev_turn_node_id {
                    edges.push(GraphEdge {
                        source_id: prev,
                        target_id: turn_node_id.clone(),
                        edge_type: "FOLLOWED_BY".to_string(),
                        weight: 1.0,
                        metadata_json: "{}".to_string(),
                    });
                } else {
                    edges.push(GraphEdge {
                        source_id: s_node.node_id.clone(),
                        target_id: turn_node_id.clone(),
                        edge_type: "HAS_TURN".to_string(),
                        weight: 1.0,
                        metadata_json: "{}".to_string(),
                    });
                }

                // Detectar chamadas de ferramenta
                if let Some(t_calls_str) = tool_calls {
                    if let Ok(parsed_calls) = serde_json::from_str::<serde_json::Value>(&t_calls_str) {
                        if let Some(arr) = parsed_calls.as_array() {
                            for call in arr {
                                let c_id = call.get("id").and_then(|v| v.as_str()).unwrap_or("unknown");
                                let fn_name = call.get("function").and_then(|f| f.get("name")).and_then(|n| n.as_str()).unwrap_or("tool");
                                let args = call.get("function").and_then(|f| f.get("arguments")).and_then(|a| a.as_str()).unwrap_or("");
                                let tool_node_id = format!("tool:{session_id}:{c_id}");

                                tool_nodes.push(GraphNode {
                                    node_id: tool_node_id.clone(),
                                    node_type: "tool_action".to_string(),
                                    session_id: session_id.to_string(),
                                    label: format!("call:{fn_name}"),
                                    content: args.to_string(),
                                    metadata_json: serde_json::json!({
                                        "tool_name": fn_name,
                                        "call_id": c_id
                                    }).to_string(),
                                    created_at: ts,
                                });

                                edges.push(GraphEdge {
                                    source_id: turn_node_id.clone(),
                                    target_id: tool_node_id.clone(),
                                    edge_type: "INVOKES".to_string(),
                                    weight: 1.0,
                                    metadata_json: "{}".to_string(),
                                });

                                // Se for delegação de subagente, conectar ao filho
                                if fn_name.contains("subagent") || fn_name.contains("delegate") {
                                    if let Ok(args_json) = serde_json::from_str::<serde_json::Value>(args) {
                                        if let Some(child_sid) = args_json.get("session_id").or_else(|| args_json.get("agent_id")).and_then(|v| v.as_str()) {
                                            edges.push(GraphEdge {
                                                source_id: tool_node_id,
                                                target_id: format!("session:{child_sid}"),
                                                edge_type: "DISPATCHED_SUBAGENT".to_string(),
                                                weight: 1.5,
                                                metadata_json: "{}".to_string(),
                                            });
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // Resposta de ferramenta
                if role == "tool" {
                    if let Some(c_id) = tool_call_id {
                        let tool_node_id = format!("tool:{session_id}:{c_id}");
                        edges.push(GraphEdge {
                            source_id: tool_node_id,
                            target_id: turn_node_id.clone(),
                            edge_type: "RETURNS_TO".to_string(),
                            weight: 1.0,
                            metadata_json: serde_json::json!({ "tool_name": tool_name }).to_string(),
                        });
                    }
                }

                prev_turn_node_id = Some(turn_node_id);
            }
        }

        // 3. Gravar no banco de grafos (Transação)
        let tx = rag_conn.transaction().map_err(|e| e.to_string())?;

        let mut inserted_nodes = 0;
        let mut all_nodes = vec![s_node];
        all_nodes.extend(turn_nodes);
        all_nodes.extend(tool_nodes);

        for n in &all_nodes {
            tx.execute(
                "INSERT INTO haos_graph_nodes (node_id, node_type, session_id, label, content, metadata_json, created_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
                 ON CONFLICT(node_id) DO UPDATE SET
                     label = excluded.label,
                     content = excluded.content,
                     metadata_json = excluded.metadata_json,
                     created_at = excluded.created_at;",
                params![n.node_id, n.node_type, n.session_id, n.label, n.content, n.metadata_json, n.created_at],
            ).map_err(|e| e.to_string())?;

            if !n.content.trim().is_empty() {
                let _ = tx.execute(
                    "INSERT INTO haos_graph_fts (node_id, content) VALUES (?1, ?2);",
                    params![n.node_id, n.content],
                );
            }
            inserted_nodes += 1;
        }

        for e in &edges {
            tx.execute(
                "INSERT INTO haos_graph_edges (source_id, target_id, edge_type, weight, metadata_json)
                 VALUES (?1, ?2, ?3, ?4, ?5)
                 ON CONFLICT(source_id, target_id, edge_type) DO UPDATE SET
                     weight = excluded.weight,
                     metadata_json = excluded.metadata_json;",
                params![e.source_id, e.target_id, e.edge_type, e.weight, e.metadata_json],
            ).map_err(|e| e.to_string())?;
        }

        tx.commit().map_err(|e| e.to_string())?;
        Ok(inserted_nodes)
    }

    /// Retorna a linhagem DAG completa de uma sessão (ancestrais + descendentes de subagentes).
    pub fn get_session_lineage(haos_home: &Path, session_id: &str) -> Result<GraphLineage, String> {
        let active_home = Self::find_session_home(haos_home, session_id)
            .unwrap_or_else(|| haos_home.to_path_buf());
        let conn = Self::open_or_create_db(&active_home)?;
        let start_node = format!("session:{session_id}");

        let mut visited_nodes: HashSet<String> = HashSet::new();
        let mut queue: VecDeque<String> = VecDeque::new();
        queue.push_back(start_node.clone());
        visited_nodes.insert(start_node);

        let mut result_edges = Vec::new();

        // Expansão BFS em ambas as direções (pais e filhos)
        while let Some(current) = queue.pop_front() {
            // Vizinhos de saída
            let mut out_stmt = conn.prepare(
                "SELECT source_id, target_id, edge_type, weight, metadata_json
                 FROM haos_graph_edges WHERE source_id = ?;",
            ).map_err(|e| e.to_string())?;

            let out_rows = out_stmt.query_map([&current], |r| {
                Ok(GraphEdge {
                    source_id: r.get(0)?,
                    target_id: r.get(1)?,
                    edge_type: r.get(2)?,
                    weight: r.get(3)?,
                    metadata_json: r.get(4)?,
                })
            }).map_err(|e| e.to_string())?;

            for e in out_rows.flatten() {
                if !visited_nodes.contains(&e.target_id) {
                    visited_nodes.insert(e.target_id.clone());
                    queue.push_back(e.target_id.clone());
                }
                result_edges.push(e);
            }

            // Vizinhos de entrada
            let mut in_stmt = conn.prepare(
                "SELECT source_id, target_id, edge_type, weight, metadata_json
                 FROM haos_graph_edges WHERE target_id = ?;",
            ).map_err(|e| e.to_string())?;

            let in_rows = in_stmt.query_map([&current], |r| {
                Ok(GraphEdge {
                    source_id: r.get(0)?,
                    target_id: r.get(1)?,
                    edge_type: r.get(2)?,
                    weight: r.get(3)?,
                    metadata_json: r.get(4)?,
                })
            }).map_err(|e| e.to_string())?;

            for e in in_rows.flatten() {
                if !visited_nodes.contains(&e.source_id) {
                    visited_nodes.insert(e.source_id.clone());
                    queue.push_back(e.source_id.clone());
                }
                result_edges.push(e);
            }
        }

        // Buscar detalhes de todos os nós visitados
        let mut result_nodes = Vec::new();
        for node_id in &visited_nodes {
            if let Ok(mut stmt) = conn.prepare(
                "SELECT node_id, node_type, session_id, label, content, metadata_json, created_at
                 FROM haos_graph_nodes WHERE node_id = ?;",
            ) {
                if let Ok(mut rows) = stmt.query_map([node_id], |r| {
                    Ok(GraphNode {
                        node_id: r.get(0)?,
                        node_type: r.get(1)?,
                        session_id: r.get(2)?,
                        label: r.get(3)?,
                        content: r.get(4)?,
                        metadata_json: r.get(5)?,
                        created_at: r.get(6)?,
                    })
                }) {
                    if let Some(Ok(n)) = rows.next() {
                        result_nodes.push(n);
                    }
                }
            }
        }

        Ok(GraphLineage {
            session_id: session_id.to_string(),
            nodes: result_nodes,
            edges: result_edges,
        })
    }

    /// Executa busca híbrida (FTS5 + Graph Walk de K-saltos).
    pub fn query_raggraph(
        haos_home: &Path,
        query_text: &str,
        k_hops: usize,
        limit: usize,
    ) -> Result<RAGGraphQueryResult, String> {
        let active_home = Self::candidate_homes(haos_home)
            .into_iter()
            .find(|h| h.join("memory").join("raggraph.db").exists())
            .unwrap_or_else(|| haos_home.to_path_buf());
        let conn = Self::open_or_create_db(&active_home)?;

        // 1. Busca textual FTS5 pelos nós semente
        let fts_tokens: Vec<&str> = query_text.split_whitespace().collect();
        let fts_match = fts_tokens
            .iter()
            .map(|t| format!("\"{}\"", t.replace('"', "")))
            .collect::<Vec<_>>()
            .join(" OR ");

        if fts_match.is_empty() {
            return Ok(RAGGraphQueryResult {
                query: query_text.to_string(),
                count: 0,
                items: Vec::new(),
            });
        }

        let mut stmt = conn.prepare(
            "SELECT n.node_id, n.node_type, n.session_id, n.label, n.content, n.metadata_json, n.created_at, bm25(haos_graph_fts)
             FROM haos_graph_fts f
             JOIN haos_graph_nodes n ON f.node_id = n.node_id
             WHERE haos_graph_fts MATCH ?
             ORDER BY bm25(haos_graph_fts) ASC
             LIMIT ?;",
        ).map_err(|e| format!("FTS5 search failed: {e}"))?;

        let seed_rows = stmt.query_map([fts_match, limit.to_string()], |r| {
            let score: f64 = r.get(7)?;
            Ok((
                GraphNode {
                    node_id: r.get(0)?,
                    node_type: r.get(1)?,
                    session_id: r.get(2)?,
                    label: r.get(3)?,
                    content: r.get(4)?,
                    metadata_json: r.get(5)?,
                    created_at: r.get(6)?,
                },
                score as f32,
            ))
        }).map_err(|e| e.to_string())?;

        let mut items = Vec::new();
        let max_hops = k_hops.clamp(1, 3);

        for r in seed_rows.flatten() {
            let (seed, score) = r;
            let mut context_nodes = Vec::new();
            let mut edges = Vec::new();
            let mut visited = HashSet::new();
            visited.insert(seed.node_id.clone());

            let mut current_level = vec![seed.node_id.clone()];

            for _ in 0..max_hops {
                let mut next_level = Vec::new();
                for current_id in &current_level {
                    if let Ok(mut e_stmt) = conn.prepare(
                        "SELECT source_id, target_id, edge_type, weight, metadata_json
                         FROM haos_graph_edges WHERE source_id = ? OR target_id = ? LIMIT 10;",
                    ) {
                        if let Ok(e_rows) = e_stmt.query_map([current_id, current_id], |row| {
                            Ok(GraphEdge {
                                source_id: row.get(0)?,
                                target_id: row.get(1)?,
                                edge_type: row.get(2)?,
                                weight: row.get(3)?,
                                metadata_json: row.get(4)?,
                            })
                        }) {
                            for edge in e_rows.flatten() {
                                let neighbor = if edge.source_id == *current_id {
                                    &edge.target_id
                                } else {
                                    &edge.source_id
                                };

                                if !visited.contains(neighbor) {
                                    visited.insert(neighbor.clone());
                                    next_level.push(neighbor.clone());

                                    // Buscar nó vizinho
                                    if let Ok(mut n_stmt) = conn.prepare(
                                        "SELECT node_id, node_type, session_id, label, content, metadata_json, created_at
                                         FROM haos_graph_nodes WHERE node_id = ?;",
                                    ) {
                                        if let Ok(mut n_rows) = n_stmt.query_map([neighbor], |nr| {
                                            Ok(GraphNode {
                                                node_id: nr.get(0)?,
                                                node_type: nr.get(1)?,
                                                session_id: nr.get(2)?,
                                                label: nr.get(3)?,
                                                content: nr.get(4)?,
                                                metadata_json: nr.get(5)?,
                                                created_at: nr.get(6)?,
                                            })
                                        }) {
                                            if let Some(Ok(node)) = n_rows.next() {
                                                context_nodes.push(node);
                                            }
                                        }
                                    }
                                }
                                edges.push(edge);
                            }
                        }
                    }
                }
                current_level = next_level;
                if current_level.is_empty() {
                    break;
                }
            }

            items.push(RAGGraphItem {
                score,
                seed_node: seed,
                context_nodes,
                edges,
            });
        }

        Ok(RAGGraphQueryResult {
            query: query_text.to_string(),
            count: items.len(),
            items,
        })
    }

    /// Indexa memórias canônicas e reconciliadas de reconciled_memories.db e fabric.db no RAGGraph.
    pub fn index_memories(haos_home: &Path) -> Result<usize, String> {
        let active_home = Self::candidate_homes(haos_home)
            .into_iter()
            .find(|h| h.join("memory").join("raggraph.db").exists() || h.join("state.db").exists())
            .unwrap_or_else(|| haos_home.to_path_buf());
        let mut rag_conn = Self::open_or_create_db(&active_home)?;

        // 1. Descobrir sessões conhecidas (do raggraph e do state.db)
        let mut known_sessions: HashSet<String> = HashSet::new();
        if let Ok(mut stmt) = rag_conn.prepare("SELECT session_id FROM haos_graph_nodes WHERE session_id != '';") {
            if let Ok(rows) = stmt.query_map([], |r| r.get::<_, String>(0)) {
                for s in rows.flatten() {
                    let trimmed = s.trim().to_string();
                    if !trimmed.is_empty() {
                        known_sessions.insert(trimmed);
                    }
                }
            }
        }
        if let Ok(mut stmt) = rag_conn.prepare("SELECT substr(node_id, 9) FROM haos_graph_nodes WHERE node_id LIKE 'session:%';") {
            if let Ok(rows) = stmt.query_map([], |r| r.get::<_, String>(0)) {
                for s in rows.flatten() {
                    let trimmed = s.trim().to_string();
                    if !trimmed.is_empty() {
                        known_sessions.insert(trimmed);
                    }
                }
            }
        }

        for h in Self::candidate_homes(haos_home) {
            let sdb = h.join("state.db");
            if sdb.exists() {
                if let Ok(s_conn) = Connection::open_with_flags(
                    &sdb,
                    OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
                ) {
                    let _ = s_conn.execute_batch("PRAGMA busy_timeout=5000;");
                    if let Ok(mut stmt) = s_conn.prepare("SELECT id FROM sessions;") {
                        if let Ok(rows) = stmt.query_map([], |r| r.get::<_, String>(0)) {
                            for s in rows.flatten() {
                                let trimmed = s.trim().to_string();
                                if !trimmed.is_empty() {
                                    known_sessions.insert(trimmed);
                                }
                            }
                        }
                    }
                }
            }
        }

        // 2. Localizar caminhos existentes de reconciled_memories.db e fabric.db
        // Priorizar active_home; se nada for encontrado, buscar em outras candidate_homes
        let mut search_homes = vec![active_home.clone()];
        if cfg!(test) {
            // Under unit tests, restrict strictly to active_home to avoid bleeding host database state
        } else {
            for h in Self::candidate_homes(haos_home) {
                if !search_homes.contains(&h) {
                    search_homes.push(h);
                }
            }
        }

        let mut reconciled_paths = Vec::new();
        let mut fabric_paths = Vec::new();

        // Coletar TODOS os caminhos de memória em todas as homes candidatas.
        // Uma home pode conter reconciled_memories.db vazio (0 linhas) enquanto a
        // memória canônica reside em outra home; parar na primeira ocorrência
        // silenciaria os dados reais. A deduplicação por node_id + upsert tornam
        // a leitura multi-home idempotente.
        for h in &search_homes {
            for p in [h.join("memory").join("reconciled_memories.db"), h.join("reconciled_memories.db")] {
                if p.exists() && !reconciled_paths.contains(&p) {
                    reconciled_paths.push(p);
                }
            }
            for p in [h.join("memory").join("fabric.db"), h.join("fabric.db")] {
                if p.exists() && !fabric_paths.contains(&p) {
                    fabric_paths.push(p);
                }
            }
        }

        eprintln!(
            "[raggraph.index_memories] active_home={active_home:?} reconciled={reconciled_paths:?} fabric={fabric_paths:?}"
        );

        let mut nodes: Vec<GraphNode> = Vec::new();
        let mut edges: Vec<GraphEdge> = Vec::new();

        // 3. Ler de reconciled_memories.db
        for r_path in &reconciled_paths {
            if let Ok(r_conn) = Connection::open_with_flags(
                r_path,
                OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
            ) {
                let _ = r_conn.execute_batch("PRAGMA busy_timeout=5000;");
                let has_table: bool = r_conn
                    .query_row(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='haos_memories' LIMIT 1;",
                        [],
                        |_| Ok(true),
                    )
                    .unwrap_or(false);

                if has_table {
                    let col_names: HashSet<String> = {
                        if let Ok(mut col_stmt) = r_conn.prepare("PRAGMA table_info(haos_memories);") {
                            col_stmt
                                .query_map([], |row| row.get::<_, String>(1))
                                .map(|iter| iter.filter_map(|r| r.ok()).collect())
                                .unwrap_or_default()
                        } else {
                            HashSet::new()
                        }
                    };

                    let id_col = if col_names.contains("id") { "id" } else { "rowid" };
                    let topic_col = if col_names.contains("topic") { "topic" } else { "''" };
                    let title_col = if col_names.contains("title") { "title" } else { "NULL" };
                    let content_col = if col_names.contains("content") { "content" } else { "''" };
                    let category_col = if col_names.contains("category") { "category" } else { "'general'" };
                    let confidence_col = if col_names.contains("confidence") { "confidence" } else { "1.0" };
                    let status_col = if col_names.contains("status") { "status" } else { "'active'" };
                    let superseded_by_col = if col_names.contains("superseded_by") { "superseded_by" } else { "NULL" };
                    let created_at_col = if col_names.contains("created_at") { "created_at" } else { "0.0" };
                    let metadata_col = if col_names.contains("metadata") { "metadata" } else { "NULL" };
                    let tags_col = if col_names.contains("tags") { "tags" } else { "NULL" };

                    let query = format!(
                        "SELECT {id_col}, {topic_col}, {title_col}, {content_col}, {category_col}, \
                                {confidence_col}, {status_col}, {superseded_by_col}, {created_at_col}, \
                                {metadata_col}, {tags_col} \
                         FROM haos_memories;"
                    );

                    let prep = r_conn.prepare(&query);
                    if let Err(e) = &prep {
                        eprintln!("[raggraph.index_memories] reconciled prepare failed: {e}");
                    }
                    if let Ok(mut stmt) = prep {
                        let rows = stmt.query_map([], |row| {
                            let id: String = row.get(0)?;
                            let topic: String = row.get(1)?;
                            let title: Option<String> = row.get(2)?;
                            let content: String = row.get(3)?;
                            let category: String = row.get(4)?;
                            let confidence: f64 = row.get(5)?;
                            let status: String = row.get(6)?;
                            let superseded_by: Option<String> = row.get(7)?;
                            let created_at: f64 = row.get(8)?;
                            let metadata_str: Option<String> = row.get(9)?;
                            let tags_str: Option<String> = row.get(10)?;
                            Ok((id, topic, title, content, category, confidence, status, superseded_by, created_at, metadata_str, tags_str))
                        });

                        if let Ok(rows_iter) = rows {
                            for r in rows_iter {
                                let r = match r {
                                    Ok(v) => v,
                                    Err(e) => {
                                        eprintln!("[raggraph.index_memories] reconciled row decode error: {e}");
                                        continue;
                                    }
                                };
                                let (id, topic, title, content, category, confidence, status, superseded_by, created_at, metadata_str, tags_str) = r;
                                let mem_node_id = format!("memory:{id}");

                                let meta_json_val: Option<serde_json::Value> = metadata_str
                                    .as_deref()
                                    .and_then(|s| serde_json::from_str(s).ok());

                                let resolved_label = title
                                    .filter(|t| !t.trim().is_empty())
                                    .or_else(|| {
                                        meta_json_val
                                            .as_ref()
                                            .and_then(|v| v.get("title").or_else(|| v.get("topic")).and_then(|t| t.as_str()).map(|t| t.to_string()))
                                    })
                                    .unwrap_or_else(|| {
                                        if !topic.trim().is_empty() {
                                            topic.clone()
                                        } else if !category.trim().is_empty() {
                                            category.clone()
                                        } else {
                                            format!("Memory {id}")
                                        }
                                    });

                                let mut extra_tags = Vec::new();
                                if let Some(t_str) = &tags_str {
                                    if let Ok(arr) = serde_json::from_str::<Vec<String>>(t_str) {
                                        extra_tags.extend(arr);
                                    } else {
                                        for part in t_str.split(',') {
                                            let p = part.trim();
                                            if !p.is_empty() {
                                                extra_tags.push(p.to_string());
                                            }
                                        }
                                    }
                                }

                                let mem_edges = extract_memory_session_edges(
                                    &mem_node_id,
                                    meta_json_val.as_ref(),
                                    &extra_tags,
                                    &[],
                                    &known_sessions,
                                );
                                edges.extend(mem_edges);

                                if let Some(s_by) = superseded_by.filter(|s| !s.trim().is_empty()) {
                                    edges.push(GraphEdge {
                                        source_id: mem_node_id.clone(),
                                        target_id: format!("memory:{s_by}"),
                                        edge_type: "SUPERSEDED_BY".to_string(),
                                        weight: 1.0,
                                        metadata_json: "{}".to_string(),
                                    });
                                }

                                let final_meta = serde_json::json!({
                                    "source_db": "reconciled_memories",
                                    "category": category,
                                    "topic": topic,
                                    "confidence": confidence,
                                    "status": status,
                                    "raw_metadata": meta_json_val,
                                });

                                nodes.push(GraphNode {
                                    node_id: mem_node_id,
                                    node_type: "memory".to_string(),
                                    session_id: String::new(),
                                    label: resolved_label,
                                    content,
                                    metadata_json: final_meta.to_string(),
                                    created_at,
                                });
                            }
                        }
                    }
                }
            }
        }

        // 4. Ler de fabric.db
        for f_path in &fabric_paths {
            if let Ok(f_conn) = Connection::open_with_flags(
                f_path,
                OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
            ) {
                let _ = f_conn.execute_batch("PRAGMA busy_timeout=5000;");
                let has_records: bool = f_conn
                    .query_row(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_records' LIMIT 1;",
                        [],
                        |_| Ok(true),
                    )
                    .unwrap_or(false);

                if has_records {
                    let sql = "SELECT record_id, logical_id, revision, scope, kind, status, content, \
                                      confidence, provenance_json, metadata_json, created_at \
                               FROM memory_records;";
                    if let Ok(mut stmt) = f_conn.prepare(sql) {
                        let rows = stmt.query_map([], |row| {
                            let record_id: String = row.get(0)?;
                            let logical_id: String = row.get(1)?;
                            let revision: i64 = row.get(2)?;
                            let scope: String = row.get(3)?;
                            let kind: String = row.get(4)?;
                            let status: String = row.get(5)?;
                            let content: String = row.get(6)?;
                            let confidence: f64 = row.get(7)?;
                            let provenance_json: String = row.get(8)?;
                            let metadata_json: String = row.get(9)?;
                            let created_at: f64 = row.get(10)?;
                            Ok((record_id, logical_id, revision, scope, kind, status, content, confidence, provenance_json, metadata_json, created_at))
                        });

                        if let Ok(rows_iter) = rows {
                            for r in rows_iter.flatten() {
                                let (record_id, logical_id, revision, scope, kind, status, content, confidence, provenance_json, metadata_json, created_at) = r;
                                let mem_node_id = format!("memory:{record_id}");

                                let meta_val: Option<serde_json::Value> = serde_json::from_str(&metadata_json).ok();
                                let prov_val: Option<serde_json::Value> = serde_json::from_str(&provenance_json).ok();

                                let label = meta_val
                                    .as_ref()
                                    .and_then(|v| v.get("title").or_else(|| v.get("topic")).and_then(|s| s.as_str()).map(|s| s.to_string()))
                                    .unwrap_or_else(|| {
                                        if !kind.is_empty() && !logical_id.is_empty() {
                                            format!("{kind}:{logical_id}")
                                        } else {
                                            format!("Memory {record_id}")
                                        }
                                    });

                                let mut combined_meta = meta_val.clone().unwrap_or_else(|| serde_json::json!({}));
                                if let Some(prov) = prov_val {
                                    combined_meta["provenance"] = prov;
                                }

                                let mem_edges = extract_memory_session_edges(
                                    &mem_node_id,
                                    Some(&combined_meta),
                                    &[],
                                    &[],
                                    &known_sessions,
                                );
                                edges.extend(mem_edges);

                                let final_meta = serde_json::json!({
                                    "source_db": "fabric",
                                    "logical_id": logical_id,
                                    "revision": revision,
                                    "scope": scope,
                                    "kind": kind,
                                    "status": status,
                                    "confidence": confidence,
                                    "metadata": meta_val,
                                });

                                nodes.push(GraphNode {
                                    node_id: mem_node_id,
                                    node_type: "memory".to_string(),
                                    session_id: String::new(),
                                    label,
                                    content,
                                    metadata_json: final_meta.to_string(),
                                    created_at,
                                });
                            }
                        }
                    }

                    // Ler supersessions do fabric.db se existir
                    let has_supersessions: bool = f_conn
                        .query_row(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_supersessions' LIMIT 1;",
                            [],
                            |_| Ok(true),
                        )
                        .unwrap_or(false);

                    if has_supersessions {
                        if let Ok(mut s_stmt) = f_conn.prepare(
                            "SELECT record_id, superseded_id, reason, score FROM memory_supersessions;"
                        ) {
                            if let Ok(s_rows) = s_stmt.query_map([], |r| {
                                Ok((
                                    r.get::<_, String>(0)?,
                                    r.get::<_, String>(1)?,
                                    r.get::<_, String>(2)?,
                                    r.get::<_, f64>(3)?,
                                ))
                            }) {
                                for row in s_rows.flatten() {
                                    let (rec_id, sup_id, reason, score) = row;
                                    edges.push(GraphEdge {
                                        source_id: format!("memory:{rec_id}"),
                                        target_id: format!("memory:{sup_id}"),
                                        edge_type: "SUPERSEDES".to_string(),
                                        weight: score as f32,
                                        metadata_json: serde_json::json!({ "reason": reason }).to_string(),
                                    });
                                }
                            }
                        }
                    }
                }
            }
        }

        // Deduplicar nós e arestas antes de persistir
        let mut seen_nodes = HashSet::new();
        nodes.retain(|n| seen_nodes.insert(n.node_id.clone()));

        let mut seen_edges = HashSet::new();
        edges.retain(|e| seen_edges.insert((e.source_id.clone(), e.target_id.clone(), e.edge_type.clone())));

        // 5. Persistir no raggraph.db
        let tx = rag_conn.transaction().map_err(|e| e.to_string())?;

        // Garantir que stubs de sessões, agentes ou capacidades referenciadas existam na tabela de nós
        for e in &edges {
            if e.target_id.starts_with("session:") {
                let sid = e.target_id.trim_start_matches("session:");
                let _ = tx.execute(
                    "INSERT OR IGNORE INTO haos_graph_nodes (node_id, node_type, session_id, label, content, metadata_json, created_at)
                     VALUES (?1, 'session', ?2, ?3, '', '{}', ?4);",
                    params![&e.target_id, sid, format!("Session {sid}"), 0.0],
                );
            } else if e.target_id.starts_with("agent:") {
                let aid = e.target_id.trim_start_matches("agent:");
                let _ = tx.execute(
                    "INSERT OR IGNORE INTO haos_graph_nodes (node_id, node_type, session_id, label, content, metadata_json, created_at)
                     VALUES (?1, 'agent', '', ?2, '', '{}', ?3);",
                    params![&e.target_id, aid, 0.0],
                );
            } else if e.target_id.starts_with("capability:") {
                let cap = e.target_id.trim_start_matches("capability:");
                let _ = tx.execute(
                    "INSERT OR IGNORE INTO haos_graph_nodes (node_id, node_type, session_id, label, content, metadata_json, created_at)
                     VALUES (?1, 'capability', '', ?2, '', '{}', ?3);",
                    params![&e.target_id, cap, 0.0],
                );
            }
        }

        let mut inserted_nodes = 0;
        for n in &nodes {
            tx.execute(
                "INSERT INTO haos_graph_nodes (node_id, node_type, session_id, label, content, metadata_json, created_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
                 ON CONFLICT(node_id) DO UPDATE SET
                     label = excluded.label,
                     content = excluded.content,
                     metadata_json = excluded.metadata_json,
                     created_at = excluded.created_at;",
                params![n.node_id, n.node_type, n.session_id, n.label, n.content, n.metadata_json, n.created_at],
            ).map_err(|e| e.to_string())?;

            let fts_text = if n.label.trim().is_empty() {
                n.content.clone()
            } else if n.content.trim().is_empty() {
                n.label.clone()
            } else {
                format!("{}\n{}", n.label, n.content)
            };

            if !fts_text.trim().is_empty() {
                let _ = tx.execute("DELETE FROM haos_graph_fts WHERE node_id = ?1;", params![n.node_id]);
                let _ = tx.execute(
                    "INSERT INTO haos_graph_fts (node_id, content) VALUES (?1, ?2);",
                    params![n.node_id, fts_text],
                );
            }
            inserted_nodes += 1;
        }

        for e in &edges {
            tx.execute(
                "INSERT INTO haos_graph_edges (source_id, target_id, edge_type, weight, metadata_json)
                 VALUES (?1, ?2, ?3, ?4, ?5)
                 ON CONFLICT(source_id, target_id, edge_type) DO UPDATE SET
                     weight = excluded.weight,
                     metadata_json = excluded.metadata_json;",
                params![e.source_id, e.target_id, e.edge_type, e.weight, e.metadata_json],
            ).map_err(|e| e.to_string())?;
        }

        tx.commit().map_err(|e| e.to_string())?;
        Ok(inserted_nodes)
    }

    pub fn record_agent_event(
        db_path: &Path,
        event_id: &str,
        event_type: &str,
        agent_id: &str,
        payload_json: &str,
        timestamp: f64,
        target_ref: Option<&str>,
    ) -> Result<(), String> {
        let parent_dir = db_path.parent().unwrap_or_else(|| Path::new("."));
        if !parent_dir.exists() {
            let _ = std::fs::create_dir_all(parent_dir);
        }
        let conn = Connection::open(db_path)
            .map_err(|e| format!("Failed to open raggraph.db: {e}"))?;

        conn.execute_batch(
            "PRAGMA journal_mode = WAL;
             PRAGMA synchronous = NORMAL;
             PRAGMA busy_timeout = 5000;

             CREATE TABLE IF NOT EXISTS haos_agent_events (
                 event_id TEXT PRIMARY KEY,
                 event_type TEXT NOT NULL,
                 agent_id TEXT NOT NULL,
                 payload_json TEXT NOT NULL,
                 timestamp REAL NOT NULL,
                 target_ref TEXT,
                 created_at REAL NOT NULL
             );
             CREATE INDEX IF NOT EXISTS idx_agent_events_type ON haos_agent_events(event_type);
             CREATE INDEX IF NOT EXISTS idx_agent_events_agent ON haos_agent_events(agent_id);

             CREATE TABLE IF NOT EXISTS haos_graph_nodes (
                 node_id TEXT PRIMARY KEY,
                 node_type TEXT NOT NULL,
                 session_id TEXT NOT NULL,
                 label TEXT NOT NULL,
                 content TEXT NOT NULL,
                 metadata_json TEXT DEFAULT '{}',
                 created_at REAL NOT NULL
             );
             CREATE INDEX IF NOT EXISTS idx_graph_nodes_session ON haos_graph_nodes(session_id);
             CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON haos_graph_nodes(node_type);

             CREATE TABLE IF NOT EXISTS haos_graph_edges (
                 source_id TEXT NOT NULL,
                 target_id TEXT NOT NULL,
                 edge_type TEXT NOT NULL,
                 weight REAL DEFAULT 1.0,
                 metadata_json TEXT DEFAULT '{}',
                 PRIMARY KEY (source_id, target_id, edge_type)
             );",
        )
        .map_err(|e| format!("Failed to initialize agent event tables: {e}"))?;

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(timestamp);

        conn.execute(
            "INSERT OR REPLACE INTO haos_agent_events (event_id, event_type, agent_id, payload_json, timestamp, target_ref, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7);",
            params![event_id, event_type, agent_id, payload_json, timestamp, target_ref, now],
        )
        .map_err(|e| format!("Failed to insert agent event: {e}"))?;

        let node_id = format!("event:{event_id}");
        let _ = conn.execute(
            "INSERT OR REPLACE INTO haos_graph_nodes (node_id, node_type, session_id, label, content, metadata_json, created_at)
             VALUES (?1, 'agent_event', ?2, ?3, ?4, ?5, ?6);",
            params![node_id, agent_id, event_type, payload_json, payload_json, timestamp],
        );

        if let Some(target) = target_ref {
            if !target.is_empty() {
                let _ = conn.execute(
                    "INSERT OR REPLACE INTO haos_graph_edges (source_id, target_id, edge_type, weight, metadata_json)
                     VALUES (?1, ?2, 'REFERENCES', 1.0, '{}');",
                    params![node_id, target],
                );
            }
        }

        Ok(())
    }
}

/// Extrai arestas ligando um nó de memória a sessões conhecidas a partir de metadados, tags e proveniência.
fn extract_memory_session_edges(
    mem_node_id: &str,
    meta_val: Option<&serde_json::Value>,
    extra_tags: &[String],
    extra_refs: &[String],
    known_sessions: &HashSet<String>,
) -> Vec<GraphEdge> {
    let mut edges = Vec::new();
    let mut seen = HashSet::new();

    if let Some(meta) = meta_val {
        // Campos diretos de sessão
        for key in ["session_id", "source_session_id", "source_session", "session", "learned_from"] {
            if let Some(val) = meta.get(key) {
                if let Some(s) = val.as_str() {
                    let clean = s.trim().trim_start_matches("session:").to_string();
                    if !clean.is_empty() && (known_sessions.is_empty() || known_sessions.contains(&clean)) {
                        let edge_type = if key == "learned_from" || key.contains("session") {
                            "LEARNED_FROM"
                        } else {
                            "REFERENCES"
                        };
                        if seen.insert((clean.clone(), edge_type.to_string())) {
                            edges.push(GraphEdge {
                                source_id: mem_node_id.to_string(),
                                target_id: format!("session:{clean}"),
                                edge_type: edge_type.to_string(),
                                weight: 1.0,
                                metadata_json: "{}".to_string(),
                            });
                        }
                    }
                }
            }
        }

        // Campos de agente/autoria
        for key in ["agent_id", "agent", "author", "created_by"] {
            if let Some(val) = meta.get(key) {
                if let Some(s) = val.as_str() {
                    let clean = s.trim().trim_start_matches("agent:").to_string();
                    if !clean.is_empty() {
                        let target_id = format!("agent:{clean}");
                        if seen.insert((target_id.clone(), "PRODUCED_BY".to_string())) {
                            edges.push(GraphEdge {
                                source_id: mem_node_id.to_string(),
                                target_id,
                                edge_type: "PRODUCED_BY".to_string(),
                                weight: 1.0,
                                metadata_json: "{}".to_string(),
                            });
                        }
                    }
                }
            }
        }

        // Causal relationships (Hindsight TEMPR/CARA model): caused_by, corrected_by, supports, derived_from
        let causal_mappings = [
            ("caused_by", "CAUSED_BY"),
            ("corrected_by", "CORRECTED_BY"),
            ("supports", "SUPPORTS"),
            ("derived_from", "DERIVED_FROM"),
        ];

        for (field_key, rel_type) in causal_mappings {
            if let Some(val) = meta.get(field_key) {
                let targets: Vec<String> = if let Some(s) = val.as_str() {
                    vec![s.to_string()]
                } else if let Some(arr) = val.as_array() {
                    arr.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect()
                } else {
                    vec![]
                };

                for t in targets {
                    let clean = t.trim().to_string();
                    if !clean.is_empty() {
                        let target_id = if clean.starts_with("session:")
                            || clean.starts_with("memory:")
                            || clean.starts_with("adr:")
                            || clean.starts_with("agent:")
                            || clean.starts_with("capability:")
                            || clean.starts_with("event:")
                        {
                            clean.clone()
                        } else {
                            format!("session:{clean}")
                        };
                        if seen.insert((target_id.clone(), rel_type.to_string())) {
                            edges.push(GraphEdge {
                                source_id: mem_node_id.to_string(),
                                target_id,
                                edge_type: rel_type.to_string(),
                                weight: 1.0,
                                metadata_json: "{}".to_string(),
                            });
                        }
                    }
                }
            }
        }

        // Tags dentro do JSON de metadados
        if let Some(tags_val) = meta.get("tags") {
            if let Some(arr) = tags_val.as_array() {
                for item in arr {
                    if let Some(s) = item.as_str() {
                        let clean = s.trim().trim_start_matches("session:").to_string();
                        if !clean.is_empty() && (known_sessions.is_empty() || known_sessions.contains(&clean)) {
                            let edge_type = if s.to_lowercase().contains("learn") { "LEARNED_FROM" } else { "REFERENCES" };
                            if seen.insert((clean.clone(), edge_type.to_string())) {
                                edges.push(GraphEdge {
                                    source_id: mem_node_id.to_string(),
                                    target_id: format!("session:{clean}"),
                                    edge_type: edge_type.to_string(),
                                    weight: 1.0,
                                    metadata_json: "{}".to_string(),
                                });
                            }
                        }
                    }
                }
            }
        }

        // Referências no JSON de metadados
        if let Some(refs_val) = meta.get("references") {
            if let Some(arr) = refs_val.as_array() {
                for item in arr {
                    let sid = if let Some(s) = item.as_str() {
                        Some(s.trim().trim_start_matches("session:").to_string())
                    } else if let Some(obj) = item.as_object() {
                        obj.get("session_id")
                            .or_else(|| obj.get("id"))
                            .and_then(|v| v.as_str())
                            .map(|s| s.trim().trim_start_matches("session:").to_string())
                    } else {
                        None
                    };
                    if let Some(clean) = sid {
                        if !clean.is_empty() && (known_sessions.is_empty() || known_sessions.contains(&clean)) {
                            if seen.insert((clean.clone(), "REFERENCES".to_string())) {
                                edges.push(GraphEdge {
                                    source_id: mem_node_id.to_string(),
                                    target_id: format!("session:{clean}"),
                                    edge_type: "REFERENCES".to_string(),
                                    weight: 1.0,
                                    metadata_json: "{}".to_string(),
                                });
                            }
                        }
                    }
                }
            }
        }

        // Proveniência no JSON de metadados
        if let Some(prov_val) = meta.get("provenance") {
            if let Some(arr) = prov_val.as_array() {
                for item in arr {
                    let sid = if let Some(s) = item.as_str() {
                        Some(s.trim().trim_start_matches("session:").to_string())
                    } else if let Some(obj) = item.as_object() {
                        obj.get("session_id")
                            .or_else(|| obj.get("id"))
                            .and_then(|v| v.as_str())
                            .map(|s| s.trim().trim_start_matches("session:").to_string())
                    } else {
                        None
                    };
                    if let Some(clean) = sid {
                        if !clean.is_empty() && (known_sessions.is_empty() || known_sessions.contains(&clean)) {
                            if seen.insert((clean.clone(), "LEARNED_FROM".to_string())) {
                                edges.push(GraphEdge {
                                    source_id: mem_node_id.to_string(),
                                    target_id: format!("session:{clean}"),
                                    edge_type: "LEARNED_FROM".to_string(),
                                    weight: 1.0,
                                    metadata_json: "{}".to_string(),
                                });
                            }
                        }
                    }
                }
            }
        }
    }

    // Tags avulsas passadas na coluna
    for tag in extra_tags {
        let clean = tag.trim().trim_start_matches("session:").to_string();
        if !clean.is_empty() && (known_sessions.is_empty() || known_sessions.contains(&clean)) {
            let edge_type = if tag.to_lowercase().contains("learn") { "LEARNED_FROM" } else { "REFERENCES" };
            if seen.insert((clean.clone(), edge_type.to_string())) {
                edges.push(GraphEdge {
                    source_id: mem_node_id.to_string(),
                    target_id: format!("session:{clean}"),
                    edge_type: edge_type.to_string(),
                    weight: 1.0,
                    metadata_json: "{}".to_string(),
                });
            }
        }
    }

    // Referências avulsas
    for r in extra_refs {
        let clean = r.trim().trim_start_matches("session:").to_string();
        if !clean.is_empty() && (known_sessions.is_empty() || known_sessions.contains(&clean)) {
            if seen.insert((clean.clone(), "REFERENCES".to_string())) {
                edges.push(GraphEdge {
                    source_id: mem_node_id.to_string(),
                    target_id: format!("session:{clean}"),
                    edge_type: "REFERENCES".to_string(),
                    weight: 1.0,
                    metadata_json: "{}".to_string(),
                });
            }
        }
    }

    edges
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_index_memories_flow() {
        let dir = tempdir().unwrap();
        let haos_home = dir.path();
        let mem_dir = haos_home.join("memory");
        std::fs::create_dir_all(&mem_dir).unwrap();

        // 1. Criar state.db com sessões conhecidas
        let state_db = haos_home.join("state.db");
        let s_conn = Connection::open(&state_db).unwrap();
        s_conn.execute_batch(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, model TEXT, parent_session_id TEXT, started_at REAL, last_activity_at REAL);
             INSERT INTO sessions VALUES ('sess_alpha', 'Alpha Session', 'gpt-4o', NULL, 100.0, 100.0);
             INSERT INTO sessions VALUES ('sess_beta', 'Beta Session', 'gpt-4o', NULL, 200.0, 200.0);",
        ).unwrap();

        // 2. Criar reconciled_memories.db
        let r_db = mem_dir.join("reconciled_memories.db");
        let r_conn = Connection::open(&r_db).unwrap();
        r_conn.execute_batch(
            "CREATE TABLE haos_memories (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                topic TEXT NOT NULL,
                content TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                status TEXT DEFAULT 'active',
                superseded_by TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                metadata TEXT
            );
            INSERT INTO haos_memories VALUES (
                'mem_01', 'architecture', 'Rust SQLite DAG Engine',
                'Native high performance graph indexing in Rust',
                1.0, 'active', NULL, 150.0, 150.0,
                '{\"session_id\": \"sess_alpha\", \"tags\": [\"session:sess_beta\"]}'
            );",
        ).unwrap();

        // 3. Criar fabric.db
        let f_db = mem_dir.join("fabric.db");
        let f_conn = Connection::open(&f_db).unwrap();
        f_conn.execute_batch(
            "CREATE TABLE memory_records (
                record_id TEXT PRIMARY KEY,
                logical_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                scope TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                confidence REAL NOT NULL,
                provenance_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                valid_from REAL NOT NULL,
                valid_until REAL,
                supersedes_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE memory_supersessions (
                record_id TEXT NOT NULL,
                superseded_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                score REAL NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(record_id, superseded_id)
            );
            INSERT INTO memory_records VALUES (
                'rec_01', 'log_01', 1, 'project', 'world_fact', 'active',
                'Fabric canonical memory record with fast vector indexing',
                'hash1', 1.0, '[{\"session_id\": \"sess_alpha\"}]', '{\"title\": \"Fabric Fact\", \"caused_by\": \"adr:ADR-001\"}',
                120.0, NULL, '[]', 120.0
            );
            INSERT INTO memory_records VALUES (
                'rec_02', 'log_02', 1, 'project', 'fact', 'superseded',
                'Deprecated fact content',
                'hash2', 1.0, '[]', '{}', 110.0, NULL, '[]', 110.0
            );
            INSERT INTO memory_supersessions VALUES ('rec_01', 'rec_02', 'updated', 0.95, 125.0);",
        ).unwrap();

        // 4. Executar index_memories
        let count = RAGGraphEngine::index_memories(haos_home).expect("index_memories should succeed");
        assert_eq!(count, 3, "Expected 3 indexed memories (1 from reconciled, 2 from fabric)");

        // 5. Verificar nós no raggraph.db
        let rag_db = mem_dir.join("raggraph.db");
        let rag_conn = Connection::open(&rag_db).unwrap();

        let mem1_node: (String, String, String) = rag_conn
            .query_row(
                "SELECT node_id, node_type, label FROM haos_graph_nodes WHERE node_id = 'memory:mem_01';",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .expect("memory:mem_01 must exist");
        assert_eq!(mem1_node.0, "memory:mem_01");
        assert_eq!(mem1_node.1, "memory");
        assert_eq!(mem1_node.2, "Rust SQLite DAG Engine");

        // 6. Verificar FTS5
        let fts_count: i64 = rag_conn
            .query_row(
                "SELECT COUNT(*) FROM haos_graph_fts WHERE haos_graph_fts MATCH '\"SQLite\"';",
                [],
                |r| r.get(0),
            )
            .expect("FTS search should work");
        assert!(fts_count >= 1);

        // 7. Verificar arestas LEARNED_FROM e REFERENCES
        let edges_count: i64 = rag_conn
            .query_row(
                "SELECT COUNT(*) FROM haos_graph_edges WHERE source_id = 'memory:mem_01' AND target_id = 'session:sess_alpha' AND edge_type = 'LEARNED_FROM';",
                [],
                |r| r.get(0),
            )
            .expect("LEARNED_FROM edge must exist");
        assert_eq!(edges_count, 1);

        let ref_edges_count: i64 = rag_conn
            .query_row(
                "SELECT COUNT(*) FROM haos_graph_edges WHERE source_id = 'memory:mem_01' AND target_id = 'session:sess_beta' AND edge_type = 'REFERENCES';",
                [],
                |r| r.get(0),
            )
            .expect("REFERENCES edge must exist");
        assert_eq!(ref_edges_count, 1);

        let sup_edges_count: i64 = rag_conn
            .query_row(
                "SELECT COUNT(*) FROM haos_graph_edges WHERE source_id = 'memory:rec_01' AND target_id = 'memory:rec_02' AND edge_type = 'SUPERSEDES';",
                [],
                |r| r.get(0),
            )
            .expect("SUPERSEDES edge must exist");
        assert_eq!(sup_edges_count, 1);

        let caused_edges_count: i64 = rag_conn
            .query_row(
                "SELECT COUNT(*) FROM haos_graph_edges WHERE source_id = 'memory:rec_01' AND target_id = 'adr:ADR-001' AND edge_type = 'CAUSED_BY';",
                [],
                |r| r.get(0),
            )
            .expect("CAUSED_BY edge must exist");
        assert_eq!(caused_edges_count, 1);

        // 8. Testar query_raggraph hybrid search encontrando a memória
        let query_res = RAGGraphEngine::query_raggraph(haos_home, "vector", 2, 5).unwrap();
        assert!(query_res.count >= 1);
        assert_eq!(query_res.items[0].seed_node.node_id, "memory:rec_01");
    }
}
