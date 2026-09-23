//! Binary CLI for deterministic ADR provenance queries.
//!
//! Vault resolution order:
//! 1. `--vault PATH` when supplied;
//! 2. `HAOS_VAULT_ADRS`;
//! 3. `$HERMES_HOME/obsidian_vault/adrs`;
//! 4. `$HOME/.haos/obsidian_vault/adrs`.
//!
//! The explicit `--vault` option is intended for reproducible tests and offline
//! use. The parser and graph implementation remain in the `haos-prov` library.

use std::env;
use std::fmt::Display;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use clap::{Parser, Subcommand};
use haos_prov::{
    check, desc, graph, normalize_adr_id, why, CheckResult, DescResult, Edge, EdgeRelation,
    GraphResult, TraceEvent, WhyResult,
};
use serde::Serialize;

const USAGE: &str = "Use `haos-prov --help` for command help.";

#[derive(Debug, Parser)]
#[command(
    name = "haos-prov",
    version,
    about = "Consulta causal determinística baseada no frontmatter PROV-O de ADRs"
)]
struct Cli {
    /// ADR directory. Overrides HAOS_VAULT_ADRS and the default path.
    #[arg(long, global = true, value_name = "PATH")]
    vault: Option<PathBuf>,

    /// Emit stable JSON instead of human-readable text.
    #[arg(long, global = true)]
    json: bool,

    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Follow causal links toward their roots.
    Why { adr: String },
    /// Show effects, descendants, and affected scopes.
    #[command(visible_alias = "effects")]
    Desc { adr: String },
    /// List graph nodes and provenance edges.
    Graph,
    /// Validate the vault, including ghosts and cycles.
    Check,
}

#[derive(Debug, Serialize)]
struct ErrorOutput<'a> {
    error: &'a str,
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    match run(cli) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

#[derive(Debug)]
struct LoadedVault {
    vault: haos_prov::Vault,
    warnings: Vec<String>,
}

fn run(cli: Cli) -> Result<(), String> {
    let json = cli.json;
    let command_name = match &cli.command {
        Command::Why { .. } => "why",
        Command::Desc { .. } => "desc",
        Command::Graph => "graph",
        Command::Check => "check",
    };
    let vault_path = resolve_vault(cli.vault.as_deref())?;
    let loaded = load_vault(&vault_path).map_err(|error| format!("{error}\n{USAGE}"))?;
    let LoadedVault { vault, warnings } = loaded;

    match cli.command {
        Command::Why { adr } => match why(&vault, &adr) {
            Some(result) => emit(&result, || render_why(&result), json),
            None => fail(
                json,
                format!("ADR '{}' não encontrada no vault.", normalize_adr_id(&adr)),
            ),
        },
        Command::Desc { adr } => match desc(&vault, &adr) {
            Some(result) => emit(&result, || render_desc(&result), json),
            None => fail(
                json,
                format!("ADR '{}' não encontrada no vault.", normalize_adr_id(&adr)),
            ),
        },
        Command::Graph => {
            let result = graph(&vault);
            emit(&result, || render_graph(&result), json)
        }
        Command::Check => {
            let mut result = check(&vault);
            result.warnings = warnings;
            if result.node_count == 0 {
                emit(&result, || render_check(&result), json)?;
                return Err("vault vazio: nenhum ADR válido foi carregado".to_owned());
            }
            if !result.warnings.is_empty() {
                emit(&result, || render_check(&result), json)?;
                return Err("falha ao analisar frontmatter: arquivo inválido".to_owned());
            }
            emit(&result, || render_check(&result), json)?;
            if !result.ghosts.is_empty() || !result.cycles.is_empty() {
                return Err(format!(
                    "{command_name}: integridade reprovada ({} entidade(s) fantasma, {} ciclo(s))",
                    result.ghosts.len(),
                    result.cycles.len()
                ));
            }
            Ok(())
        }
    }
}

fn resolve_vault(explicit: Option<&Path>) -> Result<PathBuf, String> {
    if let Some(path) = explicit {
        return Ok(path.to_path_buf());
    }
    if let Some(path) = env::var_os("HAOS_VAULT_ADRS") {
        return Ok(PathBuf::from(path));
    }
    if let Some(home) = env::var_os("HERMES_HOME") {
        return Ok(PathBuf::from(home).join("obsidian_vault/adrs"));
    }
    if let Some(home) = env::var_os("HOME") {
        return Ok(PathBuf::from(home).join(".haos/obsidian_vault/adrs"));
    }
    Err(
        "não foi possível resolver o vault: defina --vault, HAOS_VAULT_ADRS ou HERMES_HOME"
            .to_owned(),
    )
}

fn load_vault(path: &Path) -> Result<LoadedVault, String> {
    if !path.is_dir() {
        return Err(format!(
            "vault não encontrado ou não é diretório: {}",
            path.display()
        ));
    }
    let mut documents = Vec::new();
    let mut warnings = Vec::new();
    let entries = fs::read_dir(path)
        .map_err(|error| format!("não foi possível ler o vault {}: {error}", path.display()))?;
    let mut files = entries
        .map(|entry| entry.map_err(|error| error.to_string()))
        .collect::<Result<Vec<_>, _>>()?;
    files.sort_by_key(|entry| entry.file_name());
    for entry in files {
        let file_type = entry.file_type().map_err(|error| {
            format!(
                "não foi possível inspecionar {}: {error}",
                entry.path().display()
            )
        })?;
        if !file_type.is_file()
            || entry.path().extension().and_then(|ext| ext.to_str()) != Some("md")
        {
            continue;
        }
        let source = fs::read_to_string(entry.path())
            .map_err(|error| format!("não foi possível ler {}: {error}", entry.path().display()))?;
        let file = entry.file_name().to_string_lossy().into_owned();
        match haos_prov::parse_frontmatter(&source) {
            Ok(_) => documents.push((file, source)),
            Err(error) => {
                let warning = match &error {
                    haos_prov::ParseError::MissingOpeningDelimiter
                    | haos_prov::ParseError::MissingClosingDelimiter => {
                        "frontmatter_ausente".to_owned()
                    }
                    haos_prov::ParseError::InvalidYaml(_) => "yaml_malformado".to_owned(),
                    haos_prov::ParseError::NotAMapping => "frontmatter_nao_mapeamento".to_owned(),
                };
                warnings.push(format!("{file}: {warning}"));
            }
        }
    }
    let vault = if documents.is_empty() && warnings.is_empty() {
        haos_prov::parse_vault(Vec::<(String, String)>::new())
            .map_err(|_| "falha ao criar vault vazio".to_owned())?
    } else {
        haos_prov::parse_vault(documents).map_err(|errors| {
            let details = errors
                .into_iter()
                .map(|(file, error)| format!("{file}: {error}"))
                .collect::<Vec<_>>()
                .join("; ");
            format!("falha ao analisar frontmatter: {details}")
        })?
    };
    Ok(LoadedVault { vault, warnings })
}

fn emit<T: Serialize>(value: &T, text: impl FnOnce() -> String, json: bool) -> Result<(), String> {
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(value).map_err(|error| error.to_string())?
        );
    } else {
        println!("{}", text());
    }
    Ok(())
}

fn fail(json: bool, message: String) -> Result<(), String> {
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(&ErrorOutput { error: &message })
                .map_err(|error| error.to_string())?
        );
    }
    Err(message)
}

fn render_why(result: &WhyResult) -> String {
    let mut lines = vec![format!("WHY {}", result.root)];
    for event in &result.trace {
        match event {
            TraceEvent::Node { id, depth, via } => {
                lines.push(format!("{}{} ({via})", "  ".repeat(*depth), id));
            }
            TraceEvent::Unresolved { id, depth, via } => {
                lines.push(format!(
                    "{}{} [não resolvida; {via}]",
                    "  ".repeat(*depth),
                    id
                ));
            }
            TraceEvent::Cycle { id, depth } => {
                lines.push(format!("{}{} [ciclo]", "  ".repeat(*depth), id));
            }
        }
    }
    if !result.unresolved_ids.is_empty() {
        lines.push(format!(
            "não resolvidas: {}",
            result.unresolved_ids.join(", ")
        ));
    }
    lines.join("\n")
}

fn render_desc(result: &DescResult) -> String {
    let mut lines = vec![format!("DESC {}", result.root)];
    lines.push(format!(
        "superseded_by: {}",
        result.superseded_by.as_deref().unwrap_or("null")
    ));
    for (relation, ids) in &result.children {
        let values = if ids.is_empty() {
            "(none)".to_owned()
        } else {
            ids.join(", ")
        };
        lines.push(format!("{relation}: {values}"));
    }
    let affects = if result.affects.is_empty() {
        "(none)".to_owned()
    } else {
        result.affects.join(", ")
    };
    lines.push(format!("affects: {affects}"));
    lines.join("\n")
}

fn render_graph(result: &GraphResult) -> String {
    let mut lines = vec![format!(
        "GRAPH ({} nodes, {} edges)",
        result.nodes.len(),
        result.edges.len()
    )];
    lines.extend(result.nodes.iter().map(|node| format!("node {node}")));
    lines.extend(result.edges.iter().map(render_edge));
    lines.join("\n")
}

fn render_edge(edge: &Edge) -> String {
    format!(
        "{} --{}--> {}",
        edge.source,
        relation_name(&edge.relation),
        edge.target
    )
}

fn render_check(result: &CheckResult) -> String {
    let mut lines = vec![format!("CHECK: {} node(s)", result.node_count)];
    if result.ghosts.is_empty() && result.cycles.is_empty() {
        lines.push("OK: nenhum fantasma ou ciclo".to_owned());
    } else {
        lines.push(format!("ghosts: {}", result.ghosts.len()));
        lines.push(format!("cycles: {}", result.cycles.join(", ")));
    }
    lines
        .into_iter()
        .chain(
            result
                .warnings
                .iter()
                .map(|warning| format!("warning: {warning}")),
        )
        .collect::<Vec<_>>()
        .join("\n")
}

fn relation_name(relation: &EdgeRelation) -> &'static str {
    match relation {
        EdgeRelation::CausadoBy => "causado_by",
        EdgeRelation::WasDerivedFrom => "wasDerivedFrom",
        EdgeRelation::Supersedes => "supersedes",
        EdgeRelation::SupersededBy => "superseded_by",
        EdgeRelation::WasGeneratedBy => "wasGeneratedBy",
    }
}

impl Display for ErrorOutput<'_> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.error)
    }
}
