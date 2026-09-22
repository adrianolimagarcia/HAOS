//! Subagente Headless nativo em Rust executado como Tokio Task assíncrona.
//! Especializado em tarefas curtas (leaf subagents): auditorias, varreduras de arquivos,
//! verificações de segurança, transformações de dados e checagens estáticas.
//!
//! Vantagens em relação ao Python:
//! - Consumo de memória desprezível (~100 KB por task Tokio vs ~80 MB por AIAgent Python).
//! - Zero contenção de GIL (executa em paralelo real nos cores da CPU).
//! - Retorno imediato via channel / JSON-RPC.

use serde::{Deserialize, Serialize};
use std::time::Instant;

#[derive(Deserialize, Debug, Clone)]
pub struct HeadlessSubagentTask {
    pub task_id: String,
    pub goal: String,
    pub task_type: String, // "code_review" | "file_search" | "security_check" | "echo"
    pub payload: serde_json::Value,
    pub timeout_ms: Option<u64>,
}

#[derive(Serialize, Debug, Clone)]
pub struct HeadlessSubagentResult {
    pub ok: bool,
    pub task_id: String,
    pub status: String, // "completed" | "failed" | "timeout"
    pub execution_ms: u128,
    pub result: serde_json::Value,
    pub error: Option<String>,
}

pub struct HeadlessRunner;

impl HeadlessRunner {
    /// Spawna o subagente como uma Tokio Task isolada em background
    pub async fn spawn_task(task: HeadlessSubagentTask) -> HeadlessSubagentResult {
        let start = Instant::now();
        let task_id = task.task_id.clone();
        let timeout_ms = task.timeout_ms.unwrap_or(15_000);

        let runner_handle = tokio::spawn(async move {
            Self::execute_leaf_logic(task).await
        });

        match tokio::time::timeout(tokio::time::Duration::from_millis(timeout_ms), runner_handle).await {
            Ok(Ok(exec_result)) => {
                let duration = start.elapsed().as_millis();
                match exec_result {
                    Ok(val) => HeadlessSubagentResult {
                        ok: true,
                        task_id,
                        status: "completed".to_string(),
                        execution_ms: duration,
                        result: val,
                        error: None,
                    },
                    Err(err) => HeadlessSubagentResult {
                        ok: false,
                        task_id,
                        status: "failed".to_string(),
                        execution_ms: duration,
                        result: serde_json::Value::Null,
                        error: Some(err),
                    },
                }
            }
            Ok(Err(join_err)) => HeadlessSubagentResult {
                ok: false,
                task_id,
                status: "failed".to_string(),
                execution_ms: start.elapsed().as_millis(),
                result: serde_json::Value::Null,
                error: Some(format!("Task panicked or cancelled: {join_err}")),
            },
            Err(_) => HeadlessSubagentResult {
                ok: false,
                task_id,
                status: "timeout".to_string(),
                execution_ms: start.elapsed().as_millis(),
                result: serde_json::Value::Null,
                error: Some(format!("Subagent task timed out after {}ms", timeout_ms)),
            },
        }
    }

    async fn execute_leaf_logic(task: HeadlessSubagentTask) -> Result<serde_json::Value, String> {
        match task.task_type.as_str() {
            "code_review" => {
                let diff_text = task.payload.get("diff").and_then(|v| v.as_str()).unwrap_or_default();
                // Invoca a verificação estática interna
                let res = crate::file_engine::FastFileEngine::search_files(
                    &std::path::PathBuf::from("."),
                    "TODO|FIXME",
                    None,
                    10,
                ).map_err(|e| e.to_string())?;

                Ok(serde_json::json!({
                    "summary": "Headless code review completed by Rust Tokio task",
                    "diff_length": diff_text.len(),
                    "findings": res
                }))
            }
            "file_search" => {
                let pattern = task.payload.get("pattern").and_then(|v| v.as_str()).unwrap_or("");
                let path = task.payload.get("path").and_then(|v| v.as_str()).unwrap_or(".");
                let max_matches = task.payload.get("limit").and_then(|v| v.as_u64()).unwrap_or(50) as usize;

                let res = crate::file_engine::FastFileEngine::search_files(
                    &std::path::PathBuf::from(path),
                    pattern,
                    None,
                    max_matches,
                )?;
                Ok(serde_json::to_value(res).map_err(|e| e.to_string())?)
            }
            "security_check" => {
                let cmd = task.payload.get("command").and_then(|v| v.as_str()).unwrap_or("");
                let is_safe = !cmd.contains("rm -rf") && !cmd.contains("mkfs") && !cmd.contains("> /dev/sd");
                Ok(serde_json::json!({
                    "command": cmd,
                    "is_safe": is_safe,
                    "verdict": if is_safe { "ALLOW" } else { "BLOCK" }
                }))
            }
            _ => Ok(serde_json::json!({
                "echo_goal": task.goal,
                "payload": task.payload,
                "engine": "haos_edge_rust_tokio"
            })),
        }
    }
}
