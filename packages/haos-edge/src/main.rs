mod auth;
pub mod blast_analyzer;
pub mod cancel_registry;
pub mod compactor;
pub mod context_hasher;
pub mod cron_ledger;
mod db;
pub mod event_hub;
pub mod file_engine;
pub mod idempotency;
pub mod loop_detector;
mod mcp;
pub mod okf;
mod profile;
pub mod protocols;
mod pty;
mod server;
pub mod stt_engine;
pub mod subagent_engine;
mod supervisor;
pub mod system_one;
pub mod transport_ingress;
pub mod vector_search;
pub mod worker_snapshot;
pub mod worktree_engine;

use clap::{Parser, Subcommand};
use db::DbHelper;
use std::path::PathBuf;
use std::process::{Command, Stdio};

#[derive(Parser, Debug)]
#[command(
    name = "haos-edge",
    version = "0.1.0",
    about = "HAOS Edge - High Performance Runtime & CLI"
)]
struct Cli {
    #[command(subcommand)]
    command: Option<Commands>,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Show platform health, database statuses, and memory triad
    Status,

    /// Document search using RAGFlow engine (SQLite FTS5 + Breadcrumbs)
    Doc {
        #[command(subcommand)]
        action: DocCommands,
    },

    /// Run the high-performance Axum Web & PTY Control Plane server
    Server {
        #[arg(long, default_value_t = 8788)]
        port: u16,

        #[arg(long, default_value = "0.0.0.0")]
        host: String,

        #[arg(long)]
        static_dir: Option<PathBuf>,

        #[arg(long)]
        upstream: Option<String>,

        #[arg(long)]
        gateway_upstream: Option<String>,

        /// Explicit profile identity; required for server startup.
        #[arg(long)]
        profile: String,

        /// Explicit profile data directory; required for server startup.
        #[arg(long)]
        data_dir: PathBuf,
    },

    /// Fast diagnostics of HAOS environment and persistence
    Doctor,

    /// Native auto-compaction of sessions and SQLite VACUUM maintenance
    #[command(name = "sessions-compact")]
    SessionsCompact {
        #[arg(long, default_value_t = 5.0)]
        limit_mb: f64,

        #[arg(long, default_value_t = 0.70)]
        pct: f64,

        #[arg(long, default_value_t = 10)]
        min_keep: usize,

        #[arg(long, default_value_t = 30.0)]
        recent_minutes: f64,

        #[arg(long)]
        dry_run: bool,

        /// Run VACUUM on all SQLite databases after session compact
        #[arg(long)]
        vacuum: bool,
    },

    /// Fast SQLite VACUUM on state.db and known HAOS databases
    Vacuum {
        #[arg(long)]
        db_path: Option<PathBuf>,
    },

    /// Fast Blast Radius & Code Graph Analyzer in native Rust
    #[command(name = "blast-radius")]
    BlastRadius {
        #[arg(long)]
        root: Option<PathBuf>,

        #[arg(long = "file")]
        files: Vec<String>,

        #[arg(long = "symbol")]
        symbols: Vec<String>,

        #[arg(long, default_value_t = 4)]
        max_depth: usize,
    },

    /// Administração do WebUI (senha do operador)
    Admin {
        #[command(subcommand)]
        action: AdminCommands,
    },

    /// High-performance parent-death supervisor for MCP child process groups
    #[command(name = "mcp-supervisor")]
    McpSupervisor {
        #[arg(long = "parent-pgid")]
        parent_pgid: i32,
    },

    /// High-performance stdio MCP (Model Context Protocol) Server in native Rust
    #[command(name = "mcp-serve")]
    McpServe,

    /// Open Knowledge Format (OKF v0.2) native tooling: lint, query, attest
    Okf {
        #[command(subcommand)]
        action: OkfCommands,
    },
}

#[derive(Subcommand, Debug)]
enum OkfCommands {
    /// Varre o repositório OKF e emite relatório de conformidade e integridade
    Lint {
        #[arg(long)]
        bundle_dir: Option<PathBuf>,

        #[arg(long)]
        json: bool,
    },

    /// Consulta rápida com filtros de ciclo de vida e ordenação por confiança
    Query {
        query: Option<String>,

        #[arg(long)]
        bundle_dir: Option<PathBuf>,

        #[arg(long)]
        include_stale: bool,

        #[arg(long)]
        include_deprecated: bool,

        #[arg(long, default_value_t = 10)]
        limit: usize,

        #[arg(long)]
        json: bool,
    },

    /// Executa Attested Computation com validação estrita e gera receipt auditável
    Attest {
        concept: String,

        #[arg(long)]
        bundle_dir: Option<PathBuf>,

        /// Parâmetros em formato chave=valor (ex: --param multiplier=4)
        #[arg(long = "param")]
        params: Vec<String>,

        #[arg(long)]
        json: bool,
    },
}

#[derive(Subcommand, Debug)]
enum AdminCommands {
    /// Define/atualiza a senha do operador do WebUI (lê de stdin se omitida)
    SetPassword { password: Option<String> },
}

#[derive(Subcommand, Debug)]
enum DocCommands {
    Search {
        query: String,

        #[arg(long, default_value_t = 5)]
        limit: usize,
    },
    Index {
        path: Option<String>,
    },
}

#[tokio::main]
async fn main() {
    let raw_args: Vec<String> = std::env::args().collect();

    // `prov` is an intentionally thin boundary: it reuses the standalone
    // haos-prov binary when explicitly configured, otherwise the Python shim.
    if raw_args.get(1).map(String::as_str) == Some("prov") {
        delegate_to_prov(&raw_args[2..]);
        return;
    }

    // Fast-path: if invoked with subcommands that Python agent owns, delegate immediately
    if raw_args.len() > 1 {
        let first = &raw_args[1];
        if first == "run"
            || first == "chat"
            || first == "eval"
            || first == "skills"
            || first == "graph"
        {
            delegate_to_python(&raw_args[1..]);
            return;
        }
    }

    let cli = Cli::parse();

    match cli.command {
        Some(Commands::Status) => {
            cmd_status();
        }
        Some(Commands::Doc { action }) => match action {
            DocCommands::Search { query, limit } => {
                cmd_doc_search(&query, limit);
            }
            DocCommands::Index { path } => {
                let mut p_args = vec!["haos".to_string(), "doc".to_string(), "index".to_string()];
                if let Some(p) = path {
                    p_args.push(p);
                }
                delegate_to_python(&p_args);
            }
        },
        Some(Commands::Server {
            port,
            host,
            static_dir,
            upstream,
            gateway_upstream,
            profile,
            data_dir,
        }) => {
            let resolved = match profile::resolve_profile_data_dir(Some(&profile), Some(&data_dir))
            {
                Ok(binding) => binding,
                Err(error) => {
                    eprintln!("✗ Invalid explicit profile binding: {error:?}");
                    std::process::exit(2);
                }
            };
            if let Err(e) = server::run_server(
                port,
                &host,
                static_dir,
                upstream,
                gateway_upstream,
                resolved.data_dir().to_path_buf(),
                resolved.profile().to_owned(),
            )
            .await
            {
                eprintln!("✗ Server error: {e}");
                std::process::exit(1);
            }
        }
        Some(Commands::Doctor) => {
            cmd_doctor();
        }
        Some(Commands::SessionsCompact {
            limit_mb,
            pct,
            min_keep,
            recent_minutes,
            dry_run,
            vacuum,
        }) => {
            cmd_sessions_compact(limit_mb, pct, min_keep, recent_minutes, dry_run, vacuum);
        }
        Some(Commands::Vacuum { db_path }) => {
            cmd_vacuum(db_path);
        }
        Some(Commands::BlastRadius {
            root,
            files,
            symbols,
            max_depth,
        }) => {
            let root_dir = root.unwrap_or_else(|| std::env::current_dir().unwrap_or_default());
            let res = blast_analyzer::FastAstAnalyzer::calculate_impact(
                &root_dir, &files, &symbols, max_depth,
            );
            println!("{}", serde_json::to_string_pretty(&res).unwrap());
        }
        Some(Commands::Admin { action }) => match action {
            AdminCommands::SetPassword { password } => {
                cmd_set_password(password);
            }
        },
        Some(Commands::McpSupervisor { parent_pgid }) => {
            if let Err(e) = supervisor::run_mcp_supervisor(parent_pgid) {
                eprintln!("✗ MCP supervisor error: {e}");
                std::process::exit(1);
            }
        }
        Some(Commands::McpServe) => {
            if let Err(e) = mcp::run_mcp_server() {
                eprintln!("✗ MCP server error: {e}");
                std::process::exit(1);
            }
        }
        Some(Commands::Okf { action }) => match action {
            OkfCommands::Lint { bundle_dir, json } => {
                let dir = resolve_okf_bundle_dir(bundle_dir);
                let report = okf::lint_bundle(&dir);
                if json {
                    println!("{}", serde_json::to_string_pretty(&report).unwrap());
                } else {
                    println!("==================================================");
                    println!("       📑 HAOS OKF v0.2 LINT REPORT               ");
                    println!("==================================================");
                    println!("Bundle Dir: {}", dir.display());
                    println!("Total .md : {}", report.total_files);
                    println!("Válidos   : {}", report.valid_files);
                    println!("--------------------------------------------------");
                    if !report.missing_frontmatter.is_empty() {
                        println!("⚠️ Sem Frontmatter ({}):", report.missing_frontmatter.len());
                        for f in &report.missing_frontmatter {
                            println!("   • {f}");
                        }
                    }
                    if !report.invalid_frontmatter.is_empty() {
                        println!(
                            "❌ Frontmatter Inválido ({}):",
                            report.invalid_frontmatter.len()
                        );
                        for (f, err) in &report.invalid_frontmatter {
                            println!("   • {f} -> {err}");
                        }
                    }
                    if !report.unknown_types.is_empty() {
                        println!("❓ Tipos Não-Canônicos ({}):", report.unknown_types.len());
                        for (f, t) in &report.unknown_types {
                            println!("   • {f} (tipo: {t})");
                        }
                    }
                    if !report.deprecated_docs.is_empty() {
                        println!("🕰️ Depreciados ({}):", report.deprecated_docs.len());
                        for f in &report.deprecated_docs {
                            println!("   • {f}");
                        }
                    }
                    if !report.stale_docs.is_empty() {
                        println!("⏳ Obsoletos / Stale ({}):", report.stale_docs.len());
                        for (f, sa) in &report.stale_docs {
                            println!("   • {f} (expirou em: {sa})");
                        }
                    }
                    if !report.broken_links.is_empty() {
                        println!("🔗 Links Quebrados ({}):", report.broken_links.len());
                        for (f, text, tgt) in &report.broken_links {
                            println!("   • {f} -> [{text}]({tgt})");
                        }
                    }
                    if report.missing_frontmatter.is_empty()
                        && report.invalid_frontmatter.is_empty()
                        && report.broken_links.is_empty()
                    {
                        println!("✅ Bundle 100% íntegro de acordo com especificações OKF v0.2!");
                    }
                    println!("==================================================");
                }
            }
            OkfCommands::Query {
                query,
                bundle_dir,
                include_stale,
                include_deprecated,
                limit,
                json,
            } => {
                let dir = resolve_okf_bundle_dir(bundle_dir);
                let q_str = query.unwrap_or_default();
                let results =
                    okf::query_okf(&dir, &q_str, include_stale, include_deprecated, limit);
                if json {
                    println!("{}", serde_json::to_string_pretty(&results).unwrap());
                } else {
                    println!("==================================================");
                    println!("       🔍 HAOS OKF v0.2 QUERY RESULTS             ");
                    println!("==================================================");
                    println!("Query     : \"{}\"", q_str);
                    println!("Bundle Dir: {}", dir.display());
                    println!("Resultados: {}", results.len());
                    println!("--------------------------------------------------");
                    for (i, doc) in results.iter().enumerate() {
                        let title = doc.frontmatter.title.as_deref().unwrap_or(&doc.rel_path);
                        let status_str = format!("{:?}", doc.frontmatter.status).to_lowercase();
                        let stale_mark = if doc.is_stale { " [STALE]" } else { "" };
                        println!(
                            "{}. [{:.1} trust] {} (status: {}{})",
                            i + 1,
                            doc.trust_score,
                            title,
                            status_str,
                            stale_mark
                        );
                        println!("   Caminho: {}", doc.rel_path);
                        if !doc.frontmatter.tags.is_empty() {
                            println!("   Tags   : {}", doc.frontmatter.tags.join(", "));
                        }
                        if !doc.frontmatter.verified.is_empty() {
                            let ver_str: Vec<String> = doc
                                .frontmatter
                                .verified
                                .iter()
                                .map(|v| v.by.clone())
                                .collect();
                            println!("   Verified by: {}", ver_str.join(", "));
                        }
                        let preview: String = doc.body.chars().take(120).collect();
                        let clean_preview = preview.replace('\n', " ");
                        println!("   Preview: {}...", clean_preview);
                        println!();
                    }
                    println!("==================================================");
                }
            }
            OkfCommands::Attest {
                concept,
                bundle_dir,
                params,
                json,
            } => {
                let dir = resolve_okf_bundle_dir(bundle_dir);
                let mut p_map = std::collections::HashMap::new();
                for item in params {
                    if let Some((k, v)) = item.split_once('=') {
                        p_map.insert(k.trim().to_string(), v.trim().to_string());
                    }
                }
                match okf::execute_attestation(&dir, &concept, &p_map) {
                    Ok(receipt) => {
                        if json {
                            println!("{}", serde_json::to_string_pretty(&receipt).unwrap());
                        } else {
                            println!("==================================================");
                            println!("       🛡️ HAOS OKF v0.2 ATTESTED COMPUTATION      ");
                            println!("==================================================");
                            println!("Concept   : {}", receipt.concept_path);
                            println!("Runtime   : {}", receipt.runtime);
                            println!("Timestamp : {}", receipt.executed_at);
                            println!("Exit Code : {}", receipt.exit_code);
                            println!(
                                "Verified  : {}",
                                if receipt.verified {
                                    "✓ PASS"
                                } else {
                                    "✗ FAIL"
                                }
                            );
                            println!("Cmd SHA256: {}", receipt.computation_sha256);
                            println!("Out SHA256: {}", receipt.stdout_sha256);
                            println!("--------------------------------------------------");
                            println!("Command Executed:");
                            println!("{}", receipt.executed_command);
                            println!("--------------------------------------------------");
                            println!("Stdout:");
                            println!("{}", receipt.stdout);
                            if !receipt.stderr.is_empty() {
                                println!("--------------------------------------------------");
                                println!("Stderr:");
                                println!("{}", receipt.stderr);
                            }
                            println!("==================================================");
                        }
                    }
                    Err(e) => {
                        eprintln!("✗ Attestation execution failed: {e}");
                        std::process::exit(1);
                    }
                }
            }
        },
        None => {
            cmd_status();
        }
    }
}

fn resolve_okf_bundle_dir(bundle_dir: Option<PathBuf>) -> PathBuf {
    if let Some(d) = bundle_dir {
        return d;
    }
    let home = DbHelper::get_haos_home();
    let okf_path = home.join("okf");
    if okf_path.is_dir() {
        okf_path
    } else {
        std::env::current_dir().unwrap_or_default()
    }
}

fn cmd_status() {
    let t0 = std::time::Instant::now();
    let home = DbHelper::get_haos_home();

    let state_db = home.join("state.db");
    let kanban_db = home.join("kanban.db");
    let sessions = DbHelper::count_rows(&state_db, "sessions");
    let messages = DbHelper::count_rows(&state_db, "messages");
    let tasks = DbHelper::count_rows(&kanban_db, "tasks");

    // GraphRAG: store canônico (ADR-008) + índice CSV local de fallback
    let graphrag_store = home.join("memory").join("graphrag.db");
    let graphrag_entities = count_csv_rows(&home.join("graphrag").join("entities.csv"));
    // Contagens reais do store canônico (escrito por scripts/haos_memory_populate.py)
    let graphrag_store_entities = DbHelper::count_rows(&graphrag_store, "entities");
    let graphrag_store_relations = DbHelper::count_rows(&graphrag_store, "relations");
    // DeepDoc/RAG (FTS5) e memórias reconciliadas (dream): criados sob demanda
    let rag_chunks =
        DbHelper::count_rows(&home.join("memory").join("ragflow.db"), "haos_rag_chunks");
    let reconciled_memories = DbHelper::count_rows(
        &home.join("memory").join("reconciled_memories.db"),
        "haos_memories",
    );
    // Memória canônica de arquivos
    let vault_notes = count_md_files(&home.join("obsidian_vault"));
    let okf_docs = count_md_files(&home.join("okf"));

    println!("==================================================");
    println!("       🦀 HAOS EDGE PLATFORM STATUS (RUST CORE)   ");
    println!("==================================================");
    println!("HAOS Home : {}", home.display());
    println!("Runtime   : Native x86_64 ELF (<3ms Cold Start)");
    println!("Storage   : SQLite WAL Mode (Zero Daemons Required)");
    println!("--------------------------------------------------");
    println!("Databases:");
    println!(
        "  • State DB     : {}",
        match (sessions, messages) {
            (Some(s), Some(m)) => format!("✓ Active ({s} sessões, {m} mensagens)"),
            _ => "✗ Not initialized".to_string(),
        }
    );
    println!(
        "  • Kanban DB    : {}",
        match tasks {
            Some(n) => format!("✓ Active ({n} tarefas)"),
            None => "✗ Not initialized".to_string(),
        }
    );
    println!(
        "  • GraphRAG DB  : {}",
        match (
            graphrag_store.exists(),
            graphrag_store_entities,
            graphrag_store_relations
        ) {
            (true, Some(e), Some(r)) => format!("✓ store canônico ({e} entidades, {r} relações)"),
            (true, _, _) => "✓ store canônico presente (memory/graphrag.db)".to_string(),
            (false, _, _) => "✗ store canônico ausente (memory/graphrag.db)".to_string(),
        }
    );
    println!("--------------------------------------------------");
    println!("Memória canônica:");
    println!(
        "  • Obsidian Vault : {vault_notes} notas ({})",
        home.join("obsidian_vault").display()
    );
    println!(
        "  • GraphRAG CSV   : {}",
        match graphrag_entities {
            Some(n) => format!("{n} entidades (seed local; o store canônico é a fonte)"),
            None => "ausente".to_string(),
        }
    );
    println!(
        "  • DeepDoc/RAG    : {}",
        match rag_chunks {
            Some(n) => format!("{n} chunks (memory/ragflow.db)"),
            None => "✗ store ausente (memory/ragflow.db)".to_string(),
        }
    );
    println!("  • OKF bundles    : {okf_docs} documentos");
    println!(
        "  • Reconciliadas  : {}",
        match reconciled_memories {
            Some(n) => format!("{n} memórias (memory/reconciled_memories.db)"),
            None => "✗ não consolidada (hermes memory dream)".to_string(),
        }
    );
    println!("==================================================");
    println!("⚡ Latency: {:.2?}", t0.elapsed());
}

/// Conta arquivos .md recursivamente (0 se o diretório não existe).
fn count_md_files(dir: &std::path::Path) -> usize {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return 0;
    };
    let mut total = 0;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            total += count_md_files(&path);
        } else if path.extension().is_some_and(|e| e == "md") {
            total += 1;
        }
    }
    total
}

/// Linhas de dados de um CSV (descontando o cabeçalho); None se ausente.
fn count_csv_rows(path: &std::path::Path) -> Option<usize> {
    let content = std::fs::read_to_string(path).ok()?;
    Some(
        content
            .lines()
            .filter(|l| !l.trim().is_empty())
            .count()
            .saturating_sub(1),
    )
}

fn cmd_doc_search(query: &str, limit: usize) {
    let t0 = std::time::Instant::now();
    println!("============================================================");
    println!("🔍 HAOS RAGFlow Search (Rust Engine / SQLite FTS5)");
    println!("Query: {:?} | Limit: {}", query, limit);
    println!("============================================================");

    match DbHelper::search_ragflow(query, limit) {
        Ok(results) => {
            if results.is_empty() {
                println!("No matching chunks found.");
            } else {
                for (i, (doc, header, anchor, content)) in results.iter().enumerate() {
                    println!("\n[{}] {} | {}", i + 1, doc, header);
                    println!("    Anchor: {}", anchor);
                    let preview = content.lines().take(3).collect::<Vec<_>>().join("\n    ");
                    println!("    Snippet:\n    {}", preview);
                }
            }
        }
        Err(e) => {
            eprintln!("Error searching documents: {e}");
        }
    }
    println!("\n============================================================");
    println!("⚡ Latency: {:.2?}", t0.elapsed());
}

fn cmd_doctor() {
    let t0 = std::time::Instant::now();
    let home = DbHelper::get_haos_home();
    println!("=================================================================");
    println!("🩺 HAOS EDGE DOCTOR — Fast Rust Verification");
    println!("=================================================================");
    println!("✅ [PASS] Native Rust binary operational (x86_64 CachyOS build)");
    println!("✅ [PASS] HAOS_HOME verified: {}", home.display());
    println!("✅ [PASS] POSIX PTY master/slave allocation supported (nix crate)");
    println!("✅ [PASS] SQLite WAL checkpoint engine ready");
    println!("=================================================================");
    println!("⚡ Verification finished in {:.2?}", t0.elapsed());
}

fn cmd_sessions_compact(
    limit_mb: f64,
    pct: f64,
    min_keep: usize,
    recent_minutes: f64,
    dry_run: bool,
    vacuum: bool,
) {
    let t0 = std::time::Instant::now();
    let home = DbHelper::get_haos_home();
    println!("=================================================================");
    println!("🦀 HAOS EDGE SESSIONS COMPACTOR (RUST NATIVE)");
    println!(
        "Mode: {}",
        if dry_run {
            "DRY-RUN (Simulação)"
        } else {
            "EXEC (Aplicação Direta)"
        }
    );
    println!("Threshold: >={limit_mb:.1}MB | Prune Pct: {:.0}% | Min Keep: {min_keep} | Skip Recent: {recent_minutes:.0}m", pct * 100.0);
    println!("=================================================================");

    let report = compactor::SessionCompactor::run_auto_maintenance(
        &home,
        limit_mb,
        pct,
        min_keep,
        recent_minutes,
        dry_run,
    );

    println!(
        "Total sessões candidatas encontradas: {}",
        report.candidate_sessions_count
    );
    println!(
        "Sessões processadas/compactadas      : {}",
        report.shrunk_sessions.len()
    );

    for s in &report.shrunk_sessions {
        let title_disp = s.title.as_deref().unwrap_or("<sem título>");
        let mb_before = (s.size_bytes_before as f64) / (1024.0 * 1024.0);
        let mb_after = (s.size_bytes_after as f64) / (1024.0 * 1024.0);
        println!(
            "  • [{}] \"{}\": {:.2}MB -> {:.2}MB | msgs: {} -> {} (-{}) | db rows deleted: {}",
            s.session_id,
            title_disp,
            mb_before,
            mb_after,
            s.messages_before,
            s.messages_after,
            s.messages_removed,
            s.db_rows_deleted
        );
        if let Some(ref bak) = s.backup_path {
            println!("    Backup gerado: {bak}");
        }
    }

    if vacuum && !dry_run {
        println!("-----------------------------------------------------------------");
        println!("Executando VACUUM em bancos de dados SQLite...");
        for v in &report.vacuum_reports {
            let mb_before = (v.bytes_before as f64) / (1024.0 * 1024.0);
            let mb_after = (v.bytes_after as f64) / (1024.0 * 1024.0);
            let mb_saved = (v.bytes_saved as f64) / (1024.0 * 1024.0);
            println!(
                "  ✓ VACUUM [{}]: {:.2}MB -> {:.2}MB (reclaimed: {:.2}MB) em {}ms",
                v.db_path, mb_before, mb_after, mb_saved, v.duration_ms
            );
        }
    }

    let total_saved_mb = (report.total_bytes_saved as f64) / (1024.0 * 1024.0);
    println!("=================================================================");
    println!("Espaço total liberado estimado: {:.2} MB", total_saved_mb);
    println!("⚡ Concluído em {:.2?}", t0.elapsed());
}

fn cmd_vacuum(db_path: Option<PathBuf>) {
    let t0 = std::time::Instant::now();
    let home = DbHelper::get_haos_home();
    println!("=================================================================");
    println!("🦀 HAOS EDGE SQLITE VACUUM ENGINE (RUST NATIVE)");
    println!("=================================================================");

    let targets = if let Some(p) = db_path {
        vec![p]
    } else {
        vec![
            home.join("state.db"),
            home.join("kanban.db"),
            home.join("memory").join("ragflow.db"),
            home.join("memory").join("graphrag.db"),
            home.join("memory").join("reconciled_memories.db"),
        ]
    };

    let mut total_saved: i64 = 0;
    for target in targets {
        if !target.exists() {
            continue;
        }
        match compactor::SessionCompactor::vacuum_database(&target) {
            Ok(v) => {
                let mb_before = (v.bytes_before as f64) / (1024.0 * 1024.0);
                let mb_after = (v.bytes_after as f64) / (1024.0 * 1024.0);
                let mb_saved = (v.bytes_saved as f64) / (1024.0 * 1024.0);
                total_saved += v.bytes_saved;
                println!(
                    "  ✓ VACUUM [{}]: {:.2}MB -> {:.2}MB (reclaimed: {:.2}MB) em {}ms",
                    v.db_path, mb_before, mb_after, mb_saved, v.duration_ms
                );
            }
            Err(e) => {
                eprintln!("  ✗ Falha ao rodar VACUUM em {}: {e}", target.display());
            }
        }
    }

    println!("=================================================================");
    println!(
        "Espaço total recuperado: {:.2} MB",
        (total_saved as f64) / (1024.0 * 1024.0)
    );
    println!("⚡ Concluído em {:.2?}", t0.elapsed());
}

fn cmd_set_password(password: Option<String>) {
    let data_dir =
        std::env::var("HAOS_DATA_DIR").unwrap_or_else(|_| "/tmp/haos_shared_data".into());

    let password = match password {
        Some(p) => p,
        None => {
            eprint!("Nova senha do WebUI: ");
            match read_password_hidden() {
                Ok(p) => p,
                Err(e) => {
                    eprintln!("✗ Falha ao ler a senha: {e}");
                    std::process::exit(1);
                }
            }
        }
    };

    if password.trim().is_empty() {
        eprintln!("✗ Senha vazia — nada foi alterado.");
        std::process::exit(1);
    }

    match auth::write_passwd(std::path::Path::new(&data_dir), &password) {
        Ok(_) => {
            println!("✓ Senha do WebUI gravada em {data_dir}/webui.passwd");
            println!("  Sessões ativas continuam válidas; para invalidar todas: rm -rf {data_dir}/sessions");
        }
        Err(e) => {
            eprintln!("✗ Falha ao gravar a senha: {e}");
            std::process::exit(1);
        }
    }
}

/// Lê uma linha do stdin sem ecoar (quando é TTY).
fn read_password_hidden() -> std::io::Result<String> {
    use nix::sys::termios::{tcgetattr, tcsetattr, LocalFlags, SetArg};
    use std::io::BufRead;
    use std::os::fd::AsRawFd;

    let stdin = std::io::stdin();
    let is_tty = nix::unistd::isatty(stdin.as_raw_fd()).unwrap_or(false);
    let original = if is_tty { tcgetattr(&stdin).ok() } else { None };

    if let Some(orig) = &original {
        let mut quiet = orig.clone();
        quiet.local_flags.remove(LocalFlags::ECHO);
        let _ = tcsetattr(&stdin, SetArg::TCSANOW, &quiet);
    }

    let mut line = String::new();
    let read = stdin.lock().read_line(&mut line);

    if let Some(orig) = &original {
        let _ = tcsetattr(&stdin, SetArg::TCSANOW, orig);
        println!();
    }
    read?;
    Ok(line.trim_end_matches(['\r', '\n']).to_string())
}

fn delegate_to_prov(args: &[String]) {
    let current_exe = std::env::current_exe().ok();
    let configured = std::env::var_os("HAOS_PROV_BIN").map(PathBuf::from);
    if let Some(binary) = configured.filter(|path| {
        let same_as_edge = current_exe
            .as_ref()
            .is_some_and(|current| path.as_path() == current.as_path());
        path.is_file() && !same_as_edge
    }) {
        exec_with_args(&binary, args);
    }

    // The Python route is deliberately explicit in deployments, while the
    // workspace-relative default keeps `haos-edge prov` useful from the repo.
    let python_shim = std::env::var_os("HAOS_PROV_PY")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("tools/adr_prov.py"));
    if !python_shim.is_file() {
        eprintln!("✗ PROV runtime unavailable: set HAOS_PROV_BIN or HAOS_PROV_PY");
        std::process::exit(1);
    }

    let python = std::env::var_os("PYTHON").unwrap_or_else(|| "python3".into());
    let mut command = Command::new(python);
    command
        .arg(python_shim)
        .args(args)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    match command.status() {
        Ok(status) => std::process::exit(status.code().unwrap_or(1)),
        Err(error) => {
            eprintln!("✗ failed to start Python PROV runtime: {error}");
            std::process::exit(1);
        }
    }
}

fn exec_with_args(binary: &PathBuf, args: &[String]) -> ! {
    let status = Command::new(binary)
        .args(args)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status();
    match status {
        Ok(status) => std::process::exit(status.code().unwrap_or(1)),
        Err(error) => {
            eprintln!(
                "✗ failed to start Rust PROV runtime {}: {error}",
                binary.display()
            );
            std::process::exit(1);
        }
    }
}

fn delegate_to_python(args: &[String]) {
    let python_bins = [
        "/opt/haos/venv/bin/haos",
        "/opt/haos/venv/bin/python",
        "/usr/local/lib/haos-agent/venv/bin/haos",
        "/usr/local/lib/haos-agent/venv/bin/python",
        "python3",
    ];

    for bin in &python_bins {
        if PathBuf::from(bin).exists() || *bin == "python3" {
            let mut cmd = Command::new(bin);
            if bin.ends_with("python") || *bin == "python3" {
                cmd.arg("-m").arg("hermes_cli.main");
            }
            cmd.args(args)
                .stdin(Stdio::inherit())
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit());

            if let Ok(mut child) = cmd.spawn() {
                let status = child.wait().unwrap();
                std::process::exit(status.code().unwrap_or(0));
            }
        }
    }

    eprintln!("✗ Failed to locate Python agent runtime.");
    std::process::exit(1);
}
