use std::collections::BTreeMap;

use serde::Serialize;

use crate::graph::{CausalGraph, Edge, GraphIssue, TraceEvent};
use crate::model::normalize_adr_id;
use crate::Vault;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct WhyResult {
    pub root: String,
    pub trace: Vec<TraceEvent>,
    pub cycle_ids: Vec<String>,
    pub unresolved_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DescResult {
    pub root: String,
    pub superseded_by: Option<String>,
    pub supersedes: Vec<String>,
    pub children: BTreeMap<String, Vec<String>>,
    pub affects: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CheckResult {
    pub warnings: Vec<String>,
    pub ghosts: Vec<GraphIssue>,
    pub cycles: Vec<String>,
    pub nodes: Vec<String>,
    pub node_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct GraphResult {
    pub nodes: Vec<String>,
    pub edges: Vec<Edge>,
}

pub fn why(vault: &Vault, target: &str) -> Option<WhyResult> {
    let root = normalize_adr_id(target);
    let trace = vault.graph().trace(&root)?;
    let cycle_ids = trace
        .iter()
        .filter_map(|event| match event {
            TraceEvent::Cycle { id, .. } => Some(id.clone()),
            _ => None,
        })
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect();
    let unresolved_ids = trace
        .iter()
        .filter_map(|event| match event {
            TraceEvent::Unresolved { id, .. } => Some(id.clone()),
            _ => None,
        })
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect();
    Some(WhyResult {
        root,
        trace,
        cycle_ids,
        unresolved_ids,
    })
}

pub fn desc(vault: &Vault, target: &str) -> Option<DescResult> {
    let root = normalize_adr_id(target);
    let node = vault.nodes().get(&root)?;
    Some(DescResult {
        root: root.clone(),
        superseded_by: node.superseded_by.clone(),
        supersedes: node.supersedes.clone(),
        children: vault.graph().children(&root),
        affects: node.affects.clone(),
    })
}

pub fn effects(vault: &Vault, target: &str) -> Option<DescResult> {
    desc(vault, target)
}

pub fn graph(vault: &Vault) -> GraphResult {
    let graph = vault.graph();
    GraphResult {
        nodes: graph.nodes().keys().cloned().collect(),
        edges: graph.edges().to_vec(),
    }
}

pub fn check(vault: &Vault) -> CheckResult {
    let graph: CausalGraph = vault.graph();
    CheckResult {
        warnings: Vec::new(),
        ghosts: graph.ghost_references(),
        cycles: graph.cycle_ids(),
        nodes: graph.nodes().keys().cloned().collect(),
        node_count: graph.nodes().len(),
    }
}
