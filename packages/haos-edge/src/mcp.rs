//! HAOS Native MCP (Model Context Protocol) Server in Rust.
//!
//! Provides ultra-fast stdio JSON-RPC tools for Hermes agents with zero Python runtime overhead.
//! Tools exposed:
//!   - haos_doc_search: Instant SQLite FTS5 search across documentation and knowledge graph.
//!   - haos_system_facts: Hardware telemetry and memory statistics.
//!   - haos_tasks_list: Kanban tasks read directly from SQLite.

use crate::db::DbHelper;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};

#[derive(Deserialize, Debug)]
#[allow(dead_code)]
struct JsonRpcRequest {
    jsonrpc: String,
    id: Option<Value>,
    method: String,
    params: Option<Value>,
}

#[derive(Serialize, Debug)]
struct JsonRpcResponse {
    jsonrpc: &'static str,
    id: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<Value>,
}

pub fn run_mcp_server() -> Result<(), Box<dyn std::error::Error>> {
    let stdin = std::io::stdin();
    let mut reader = BufReader::new(stdin.lock());
    let mut stdout = std::io::stdout();
    let mut line = String::new();

    loop {
        line.clear();
        match reader.read_line(&mut line) {
            Ok(0) => break, // EOF
            Ok(_) => {
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }
                if let Ok(req) = serde_json::from_str::<JsonRpcRequest>(trimmed) {
                    if let Some(resp) = handle_request(req) {
                        let serialized = serde_json::to_string(&resp)?;
                        writeln!(stdout, "{serialized}")?;
                        stdout.flush()?;
                    }
                }
            }
            Err(_) => break,
        }
    }
    Ok(())
}

fn handle_request(req: JsonRpcRequest) -> Option<JsonRpcResponse> {
    let req_id = match req.id {
        Some(id) => id,
        None => return None, // Notification, no response needed
    };

    match req.method.as_str() {
        "initialize" => Some(JsonRpcResponse {
            jsonrpc: "2.0",
            id: req_id,
            result: Some(json!({
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "haos-edge-mcp",
                    "version": "0.1.0"
                }
            })),
            error: None,
        }),
        "notifications/initialized" => None,
        "ping" => Some(JsonRpcResponse {
            jsonrpc: "2.0",
            id: req_id,
            result: Some(json!({})),
            error: None,
        }),
        "tools/list" => Some(JsonRpcResponse {
            jsonrpc: "2.0",
            id: req_id,
            result: Some(json!({
                "tools": [
                    {
                        "name": "haos_doc_search",
                        "description": "Busca ultra-rapida na documentacao e base de conhecimento do HAOS usando SQLite FTS5 nativo em Rust (<2ms).",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Termo de busca (palavras-chave)"
                                },
                                "limit": {
                                    "type": "integer",
                                    "description": "Maximo de resultados (padrao: 5)",
                                    "default": 5
                                }
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "haos_system_facts",
                        "description": "Coleta telemetria em tempo real do sistema (memoria, processador, GPU NVIDIA e uptime) sem subprocessos.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {}
                        }
                    },
                    {
                        "name": "haos_tasks_list",
                        "description": "Lista tarefas ativas do Kanban operacional diretamente do SQLite canônico.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "limit": {
                                    "type": "integer",
                                    "description": "Quantidade de tarefas (padrao: 10)",
                                    "default": 10
                                }
                            }
                        }
                    }
                ]
            })),
            error: None,
        }),
        "tools/call" => {
            let params = req.params.unwrap_or_default();
            let name = params.get("name").and_then(|n| n.as_str()).unwrap_or("");
            let args = params.get("arguments").cloned().unwrap_or(json!({}));

            let (text, is_error) = call_tool(name, args);
            Some(JsonRpcResponse {
                jsonrpc: "2.0",
                id: req_id,
                result: Some(json!({
                    "content": [
                        {
                            "type": "text",
                            "text": text
                        }
                    ],
                    "isError": is_error
                })),
                error: None,
            })
        }
        _ => Some(JsonRpcResponse {
            jsonrpc: "2.0",
            id: req_id,
            result: None,
            error: Some(json!({
                "code": -32601,
                "message": format!("Method '{}' not found", req.method)
            })),
        }),
    }
}

fn call_tool(name: &str, args: Value) -> (String, bool) {
    match name {
        "haos_doc_search" => {
            let q = args.get("query").and_then(|v| v.as_str()).unwrap_or("");
            if q.is_empty() {
                return ("Erro: parametro 'query' obrigatorio".to_string(), true);
            }
            let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(5) as usize;
            match DbHelper::search_ragflow(q, limit) {
                Ok(results) => {
                    if results.is_empty() {
                        return (format!("Nenhum resultado encontrado para: {q}"), false);
                    }
                    let mut out = format!("# Resultados da busca para: '{q}' ({})\n\n", results.len());
                    for (i, (doc_path, header_path, anchor, content)) in results.into_iter().enumerate() {
                        out.push_str(&format!(
                            "### [{}] {}\n- **Arquivo:** {}\n- **Âncora:** {}\n\n```text\n{}\n```\n\n",
                            i + 1, header_path, doc_path, anchor, content.trim()
                        ));
                    }
                    (out, false)
                }
                Err(e) => (format!("Erro na busca SQLite FTS5: {e}"), true),
            }
        }
        "haos_system_facts" => {
            let data_dir = std::path::PathBuf::from(
                std::env::var("HAOS_DATA_DIR").unwrap_or_else(|_| "/tmp/haos_shared_data".into()),
            );
            let state = DbHelper::get_state_payload(&data_dir);
            let facts = json!({
                "mode": state.get("meta").and_then(|m| m.get("mode")).unwrap_or(&json!("haos-edge-rust")),
                "total_tasks": state.get("taskboard").and_then(|t| t.get("total")).unwrap_or(&json!(0)),
                "recent_tasks_count": state.get("taskboard").and_then(|t| t.get("tasks")).and_then(|t| t.as_array()).map(|a| a.len()).unwrap_or(0),
                "events_count": state.get("events").and_then(|e| e.as_array()).map(|a| a.len()).unwrap_or(0),
            });
            (serde_json::to_string_pretty(&facts).unwrap_or_default(), false)
        }
        "haos_tasks_list" => {
            match DbHelper::get_tasks() {
                Ok(tasks) => (serde_json::to_string_pretty(&tasks).unwrap_or_default(), false),
                Err(e) => (format!("Erro ao ler tarefas: {e}"), true),
            }
        }
        _ => (format!("Ferramenta desconhecida: {name}"), true),
    }
}
