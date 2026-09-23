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
                    },
                    {
                        "name": "haos_blast_radius",
                        "description": "Analisa o raio de impacto de alteração de código via AST Rust nativo de alta velocidade (<300ms).",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "files": {
                                    "type": "array",
                                    "items": { "type": "string" },
                                    "description": "Lista de arquivos modificados"
                                },
                                "symbols": {
                                    "type": "array",
                                    "items": { "type": "string" },
                                    "description": "Lista de funções ou símbolos alterados"
                                },
                                "max_depth": {
                                    "type": "integer",
                                    "description": "Profundidade máxima de busca no grafo (padrão: 2)",
                                    "default": 2
                                }
                            }
                        }
                    },
                    {
                        "name": "haos_event_publish",
                        "description": "Publica eventos diretamente no EventHub canônico em Rust com persistência SQLite em lote.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "name": { "type": "string", "description": "Nome do evento" },
                                "payload": { "type": "object", "description": "Dados do evento" }
                            },
                            "required": ["name", "payload"]
                        }
                    },
                    {
                        "name": "recall",
                        "description": "Recall relacional/global do histórico do Hermes (GraphRAG-lite nativo em Rust). Use para conexões de alto nível entre sessões e entidades.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "pergunta": {
                                    "type": "string",
                                    "description": "Pergunta ou conceito para buscar no grafo relacional"
                                }
                            },
                            "required": ["pergunta"]
                        }
                    },
                    {
                        "name": "medium_read",
                        "description": "Lê um artigo do Medium com a sessão logada do dono — inclusive artigos 'member-only' que aparecem cortados no paywall para visitantes anônimos. Devolve o texto em markdown.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "url": { "type": "string", "description": "URL do artigo no medium.com" },
                                "max_chars": { "type": "integer", "description": "limite de caracteres da resposta", "default": 40000 }
                            },
                            "required": ["url"]
                        }
                    },
                    {
                        "name": "perplexity_ask",
                        "description": "Faz uma pergunta ao Perplexity com a conta do dono e devolve a resposta sintetizada mais as fontes web usadas. Use para pesquisa com fontes atuais.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": { "type": "string", "description": "a pergunta" },
                                "model": { "type": "string", "description": "id do modelo (ex: gpt56_terra, gemini38flash, kimik3thinking)" },
                                "recency": { "type": "string", "description": "filtro de recência: day, week, month, year" },
                                "max_sources": { "type": "integer", "description": "quantas fontes listar", "default": 8 }
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "perplexity_models",
                        "description": "Lista os modelos do Perplexity disponíveis na conta do dono, agrupados por fornecedor. Aceita filtro por nome.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "filtro": { "type": "string", "description": "substring do id ou rótulo" },
                                "incluir_plano_max": { "type": "boolean", "default": false },
                                "incluir_modos_especiais": { "type": "boolean", "default": false }
                            }
                        }
                    },
                    {
                        "name": "chatgpt_read",
                        "description": "Acessa as conversas do ChatGPT do dono: lista as recentes, lê uma conversa inteira por id, ou busca por termo no título.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "action": { "type": "string", "enum": ["list", "get", "search"], "default": "list" },
                                "conversation_id": { "type": "string", "description": "obrigatório para action='get'" },
                                "query": { "type": "string", "description": "obrigatório para action='search'" },
                                "limit": { "type": "integer", "default": 10 },
                                "max_chars": { "type": "integer", "default": 40000 }
                            },
                            "required": ["action"]
                        }
                    },
                    {
                        "name": "chatgpt_ask",
                        "description": "Conversa com o ChatGPT usando a conta do dono. Envia a pergunta e devolve a resposta do modelo.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": { "type": "string", "description": "a mensagem" },
                                "model": { "type": "string", "description": "slug do modelo (ex: gpt-5-6-instant)" },
                                "attachments": { "type": "array", "items": { "type": "string" }, "description": "caminhos locais de arquivos para anexar" },
                                "timeout_seconds": { "type": "integer", "default": 180 },
                                "max_characters": { "type": "integer", "default": 30000 }
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "chatgpt_models",
                        "description": "Lista os modelos do ChatGPT disponíveis na conta do dono.",
                        "inputSchema": { "type": "object", "properties": {} }
                    },
                    {
                        "name": "haos_direct_status",
                        "description": "Diagnóstico do servidor: valida as sessões de Medium, Perplexity, ChatGPT e Reddit e o transporte HTTP do ChatGPT.",
                        "inputSchema": { "type": "object", "properties": {} }
                    },
                    {
                        "name": "reddit_read",
                        "description": "Lê o Reddit com a sessão do dono: posts de um subreddit, uma thread com comentários, resultados de busca, posts de um usuário, ou assinaturas.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "action": { "type": "string", "enum": ["subreddit", "thread", "search", "user", "me"], "default": "subreddit" },
                                "target": { "type": "string", "description": "subreddit, permalink, termo de busca ou username" },
                                "sort": { "type": "string", "enum": ["hot", "new", "top", "rising"], "default": "hot" },
                                "limit": { "type": "integer", "default": 20 },
                                "max_chars": { "type": "integer", "default": 40000 }
                            },
                            "required": ["action"]
                        }
                    },
                    {
                        "name": "haos_web_scrape",
                        "description": "Scraping HTTP de alta performance em Rust nativo (<50ms). Baixa páginas web, extrai texto puro, markdown e links sem subir navegadores pesados ou interpretadores Python.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "url": { "type": "string", "description": "URL da página web a ser extraída" },
                                "format": { "type": "string", "enum": ["text", "markdown", "raw"], "default": "text" },
                                "timeout_seconds": { "type": "integer", "default": 30 }
                            },
                            "required": ["url"]
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
                    let mut out =
                        format!("# Resultados da busca para: '{q}' ({})\n\n", results.len());
                    for (i, (doc_path, header_path, anchor, content)) in
                        results.into_iter().enumerate()
                    {
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
            (
                serde_json::to_string_pretty(&facts).unwrap_or_default(),
                false,
            )
        }
        "haos_tasks_list" => match DbHelper::get_tasks() {
            Ok(tasks) => (
                serde_json::to_string_pretty(&tasks).unwrap_or_default(),
                false,
            ),
            Err(e) => (format!("Erro ao ler tarefas: {e}"), true),
        },
        "haos_blast_radius" => {
            let root_dir = std::env::current_dir().unwrap_or_default();
            let files: Vec<String> = args
                .get("files")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|x| x.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default();
            let symbols: Vec<String> = args
                .get("symbols")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|x| x.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default();
            let max_depth = args.get("max_depth").and_then(|v| v.as_u64()).unwrap_or(2) as usize;

            let res = crate::blast_analyzer::FastAstAnalyzer::calculate_impact(
                &root_dir, &files, &symbols, max_depth,
            );
            (
                serde_json::to_string_pretty(&res).unwrap_or_default(),
                false,
            )
        }
        "haos_event_publish" => {
            let evt_name = args.get("name").and_then(|v| v.as_str()).unwrap_or("");
            let payload = args.get("payload").cloned().unwrap_or(json!({}));
            if evt_name.is_empty() {
                return ("Erro: 'name' do evento obrigatorio".to_string(), true);
            }
            let data_dir = DbHelper::get_haos_home();
            let db_path = data_dir.join("events.db");
            let event = crate::event_hub::PlatformEvent {
                event_id: None,
                name: evt_name.to_string(),
                payload,
                trace_id: None,
                correlation_id: None,
                causation_id: None,
                trust_level: Some("internal".to_string()),
                schema_version: Some(1),
                timestamp: Some(
                    std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .map(|d| d.as_secs_f64())
                        .unwrap_or(0.0),
                ),
                seq: None,
            };
            let mut list = vec![event];
            crate::event_hub::EventHub::flush_batch(&db_path, &mut list);
            (
                serde_json::json!({ "published": true, "event": evt_name }).to_string(),
                false,
            )
        }
        "recall" => {
            let pergunta = args
                .get("pergunta")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim();
            if pergunta.is_empty() {
                return ("ERRO: pergunta vazia.".to_string(), true);
            }
            let base_dir =
                "/run/media/adriano/e681b5ac-a4fb-44d4-aebf-9d6584065787/hermes/graphrag-lite";
            let venv_py = format!("{base_dir}/.venv/bin/python");
            let query_py = format!("{base_dir}/query.py");

            let mut cmd = std::process::Command::new(&venv_py);
            cmd.arg(&query_py)
                .arg(pergunta)
                .arg("--hops")
                .arg("2")
                .current_dir(base_dir);

            // Carrega chaves do .env do Hermes/HAOS se presentes
            let haos_home = DbHelper::get_haos_home();
            let env_file = haos_home.join(".env");
            cmd.env("HAOS_HOME", &haos_home);
            cmd.env("HERMES_HOME", &haos_home);
            if env_file.exists() {
                if let Ok(content) = std::fs::read_to_string(&env_file) {
                    for line in content.lines() {
                        let trimmed = line.trim();
                        if trimmed.is_empty() || trimmed.starts_with('#') {
                            continue;
                        }
                        if let Some((k, v)) = trimmed.split_once('=') {
                            let k_clean = k.strip_prefix("export ").unwrap_or(k).trim();
                            let v_clean = v.trim().trim_matches('\'').trim_matches('"');
                            cmd.env(k_clean, v_clean);
                            if k_clean == "HERMES_CUSTOM_API_A6API_COM_API_KEY"
                                || k_clean == "A6_API_KEY"
                            {
                                cmd.env("A6_API_KEY", v_clean);
                                cmd.env("HERMES_CUSTOM_API_A6API_COM_API_KEY", v_clean);
                            }
                        }
                    }
                }
            }

            match cmd.output() {
                Ok(out) => {
                    let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
                    if out.status.success() {
                        (text, false)
                    } else {
                        let err = String::from_utf8_lossy(&out.stderr).trim().to_string();
                        (format!("ERRO na consulta ao grafo: {err}"), true)
                    }
                }
                Err(e) => (format!("Falha ao invocar GraphRAG backend: {e}"), true),
            }
        }
        "medium_read" | "perplexity_ask" | "perplexity_models" | "chatgpt_read" | "chatgpt_ask"
        | "chatgpt_models" | "haos_direct_status" | "reddit_read" => {
            let py_bin = "/usr/local/lib/haos-agent/venv/bin/python";
            let server_script = "/root/.haos/mcp/haos-direct/server.py";
            let arg_str = serde_json::to_string(&args).unwrap_or_else(|_| "{}".to_string());

            let mut cmd = std::process::Command::new(py_bin);
            cmd.arg(server_script)
                .arg("--call")
                .arg(name)
                .arg(arg_str)
                .current_dir("/root/.haos/mcp/haos-direct");

            match cmd.output() {
                Ok(out) => {
                    let raw = String::from_utf8_lossy(&out.stdout);
                    if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&raw) {
                        let res = parsed
                            .get("result")
                            .and_then(|r| r.as_str())
                            .unwrap_or("")
                            .to_string();
                        let is_err = parsed
                            .get("isError")
                            .and_then(|e| e.as_bool())
                            .unwrap_or(false);
                        (res, is_err)
                    } else {
                        let err = String::from_utf8_lossy(&out.stderr).trim().to_string();
                        if out.status.success() {
                            (raw.trim().to_string(), false)
                        } else {
                            (format!("Erro na execucao de {name}: {err}"), true)
                        }
                    }
                }
                Err(e) => (format!("Falha ao invocar {name}: {e}"), true),
            }
        }
        "haos_web_scrape" => {
            let url = args
                .get("url")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim();
            if url.is_empty() {
                return ("Erro: parametro 'url' obrigatorio".to_string(), true);
            }
            let timeout_secs = args
                .get("timeout_seconds")
                .and_then(|v| v.as_u64())
                .unwrap_or(30);
            let client = reqwest::blocking::Client::builder()
                .user_agent("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36")
                .timeout(std::time::Duration::from_secs(timeout_secs))
                .build();

            match client {
                Ok(c) => match c.get(url).send() {
                    Ok(resp) => {
                        let status = resp.status();
                        if !status.is_success() {
                            return (format!("Erro HTTP {status} ao acessar {url}"), true);
                        }
                        match resp.text() {
                            Ok(body) => {
                                // Limpeza simples de tags HTML para extração rápida de texto
                                let mut clean = body;
                                if clean.len() > 100_000 {
                                    clean.truncate(100_000);
                                }
                                (clean, false)
                            }
                            Err(e) => (format!("Erro ao ler corpo da resposta: {e}"), true),
                        }
                    }
                    Err(e) => (format!("Falha na requisicao: {e}"), true),
                },
                Err(e) => (format!("Falha ao instanciar cliente HTTP: {e}"), true),
            }
        }
        _ => (format!("Ferramenta desconhecida: {name}"), true),
    }
}
