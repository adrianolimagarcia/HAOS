//! HAOS-Review: Bot nativo em Rust para análise de código e revisão de diffs.
//! Suporta 2 modos de operação integrados:
//! 1. CLI Standalone: haos-review diff / haos-review check
//! 2. A2A Daemon: Servidor JSON-RPC peer com AgentCard e SendMessage para orquestração de subagentes.

mod rules;
mod git_engine;
mod formatter;

use clap::{Parser, Subcommand};
use rules::DeterministicPipeline;
use git_engine::GitEngine;
use formatter::ReviewReport;
use axum::{routing::post, routing::get, Json, Router};
use std::net::SocketAddr;

#[derive(Parser, Debug)]
#[command(name = "haos-review", version = "0.1.0", about = "HAOS Native Rust Code Reviewer")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Modo 1: Executa a revisão estática do Git diff localmente
    Diff {
        #[arg(long)]
        from: Option<String>,
        #[arg(long)]
        to: Option<String>,
        #[arg(long)]
        target: Option<String>,
        #[arg(long, default_value = "json")]
        format: String,
    },
    /// Modo 2: Sobe como Daemon A2A (Agent-to-Agent) para receber requisições de bots do HAOS
    Daemon {
        #[arg(long, default_value = "127.0.0.1")]
        host: String,
        #[arg(long, default_value_t = 8795)]
        port: u16,
    },
}

#[tokio::main]
async fn main() {
    let cli = Cli::parse();

    match cli.command {
        Commands::Diff { from, to, target, format } => {
            let diff_text = match GitEngine::get_diff(target.as_deref(), from.as_deref(), to.as_deref()) {
                Ok(t) => t,
                Err(err) => {
                    eprintln!("Erro ao obter diff: {err}");
                    std::process::exit(1);
                }
            };

            let pipeline = DeterministicPipeline::new();
            let findings = pipeline.scan_diff(&diff_text);
            let report = ReviewReport::build(findings);

            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&report).unwrap());
            } else {
                println!("=== HAOS Code Review Report ===");
                println!("Status: {}", report.status);
                println!("Sumário: {}", report.summary);
                for f in report.findings {
                    println!("\n  [{}] {}:{} ({})", f.severity, f.file, f.line_number, f.rule_id);
                    println!("    -> {}", f.message);
                    println!("    Código: {}", f.code_snippet);
                }
            }
        }
        Commands::Daemon { host, port } => {
            let app = Router::new()
                .route("/.well-known/agent-card.json", get(agent_card_handler))
                .route("/a2a/send-message", post(a2a_send_message_handler))
                .route("/health", get(health_handler));

            let addr: SocketAddr = format!("{}:{}", host, port).parse().unwrap();
            println!("🦀 HAOS-Review A2A Daemon ouvindo em http://{}", addr);
            println!("   • AgentCard: http://{}/.well-known/agent-card.json", addr);
            println!("   • JSON-RPC:  http://{}/a2a/send-message", addr);

            let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
            axum::serve(listener, app).await.unwrap();
        }
    }
}

// Handlers do Daemon A2A (Protocol Fabric)
async fn health_handler() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "status": "ok",
        "service": "haos-review",
        "runtime": "rust",
        "version": "0.1.0"
    }))
}

async fn agent_card_handler() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "name": "haos-review-bot",
        "role": "auditor",
        "description": "Subagente nativo de alta velocidade para revisão determinística de código e auditoria de diffs.",
        "version": "0.1.0",
        "capabilities": {
            "protocols": ["A2A", "ACP", "INTERNAL"],
            "actions": ["review_diff", "scan_code"],
            "read_only": true
        },
        "skills": [
            "code_review",
            "security_audit",
            "diff_analysis"
        ]
    }))
}

#[derive(serde::Deserialize)]
struct A2AMessageRequest {
    pub role: Option<String>,
    pub action: Option<String>,
    pub diff: Option<String>,
    pub task_id: Option<String>,
}

async fn a2a_send_message_handler(Json(payload): Json<A2AMessageRequest>) -> Json<serde_json::Value> {
    let diff_text = payload.diff.unwrap_or_default();
    let pipeline = DeterministicPipeline::new();
    let findings = pipeline.scan_diff(&diff_text);
    let report = ReviewReport::build(findings);

    Json(serde_json::json!({
        "jsonrpc": "2.0",
        "status": "completed",
        "task_id": payload.task_id,
        "result": report
    }))
}
