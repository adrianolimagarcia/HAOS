//! Deterministic PROV-O-inspired causal queries for ADR frontmatter.
//!
//! The library deliberately exposes structured results. Text rendering and the
//! Python compatibility shim belong to later migration phases.

mod commands;
mod graph;
mod model;
mod parser;
mod vault;

pub use commands::{
    check, desc, effects, graph, why, CheckResult, DescResult, GraphResult, WhyResult,
};
pub use graph::{CausalGraph, Edge, EdgeRelation, GraphIssue, TraceEvent};
pub use model::{normalize_adr_id, AdrFrontmatter, ProvBlock, ProvNode};
pub use parser::{parse_frontmatter, parse_vault, ParseError, ParsedDocument};
pub use vault::Vault;
