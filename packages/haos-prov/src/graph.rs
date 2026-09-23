use std::collections::{BTreeMap, BTreeSet};

use serde::Serialize;

use crate::model::{normalize_adr_id, ProvNode};

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum EdgeRelation {
    CausadoBy,
    WasDerivedFrom,
    Supersedes,
    SupersededBy,
    WasGeneratedBy,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Edge {
    pub source: String,
    pub relation: EdgeRelation,
    pub target: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub enum GraphIssue {
    GhostReference {
        source: String,
        relation: EdgeRelation,
        target: String,
    },
    Cycle {
        id: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "event", rename_all = "camelCase")]
pub enum TraceEvent {
    Node {
        id: String,
        depth: usize,
        via: String,
    },
    Unresolved {
        id: String,
        depth: usize,
        via: String,
    },
    Cycle {
        id: String,
        depth: usize,
    },
}

/// Deterministic adjacency graph. Edges point from a node to its causal source.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CausalGraph {
    nodes: BTreeMap<String, ProvNode>,
    edges: Vec<Edge>,
}

impl CausalGraph {
    pub fn new(nodes: BTreeMap<String, ProvNode>) -> Self {
        let mut edges = Vec::new();
        for (id, node) in &nodes {
            for value in &node.causado_by {
                if value.to_ascii_lowercase().starts_with("adr:") {
                    edges.push(Edge {
                        source: id.clone(),
                        relation: EdgeRelation::CausadoBy,
                        target: normalize_adr_id(value),
                    });
                }
            }
            for value in &node.prov.was_derived_from {
                edges.push(Edge {
                    source: id.clone(),
                    relation: EdgeRelation::WasDerivedFrom,
                    target: if value.to_ascii_lowercase().starts_with("adr:") {
                        normalize_adr_id(value)
                    } else {
                        value.clone()
                    },
                });
            }
            for value in &node.supersedes {
                edges.push(Edge {
                    source: id.clone(),
                    relation: EdgeRelation::Supersedes,
                    target: normalize_adr_id(value),
                });
            }
            if let Some(value) = &node.superseded_by {
                edges.push(Edge {
                    source: id.clone(),
                    relation: EdgeRelation::SupersededBy,
                    target: normalize_adr_id(value),
                });
            }
            if let Some(value) = &node.prov.was_generated_by {
                edges.push(Edge {
                    source: id.clone(),
                    relation: EdgeRelation::WasGeneratedBy,
                    target: value.clone(),
                });
            }
        }
        Self { nodes, edges }
    }

    pub fn nodes(&self) -> &BTreeMap<String, ProvNode> {
        &self.nodes
    }

    pub fn edges(&self) -> &[Edge] {
        &self.edges
    }

    pub fn ghost_references(&self) -> Vec<GraphIssue> {
        self.edges
            .iter()
            .filter(|edge| {
                matches!(
                    edge.relation,
                    EdgeRelation::CausadoBy
                        | EdgeRelation::WasDerivedFrom
                        | EdgeRelation::Supersedes
                        | EdgeRelation::SupersededBy
                ) && is_canonical_adr_id(&edge.target)
                    && !self.nodes.contains_key(&edge.target)
            })
            .map(|edge| GraphIssue::GhostReference {
                source: edge.source.clone(),
                relation: edge.relation.clone(),
                target: edge.target.clone(),
            })
            .collect()
    }

    pub fn trace(&self, root: &str) -> Option<Vec<TraceEvent>> {
        let root = normalize_adr_id(root);
        if !self.nodes.contains_key(&root) {
            return None;
        }
        let mut trace = Vec::new();
        let mut visited = BTreeSet::new();
        self.visit(&root, 0, "root", &mut visited, &mut trace);
        Some(trace)
    }

    fn visit(
        &self,
        id: &str,
        depth: usize,
        via: &str,
        visited: &mut BTreeSet<String>,
        trace: &mut Vec<TraceEvent>,
    ) {
        let Some(_node) = self.nodes.get(id) else {
            trace.push(TraceEvent::Unresolved {
                id: id.to_owned(),
                depth,
                via: via.to_owned(),
            });
            return;
        };
        trace.push(TraceEvent::Node {
            id: id.to_owned(),
            depth,
            via: via.to_owned(),
        });
        if !visited.insert(id.to_owned()) {
            trace.push(TraceEvent::Cycle {
                id: id.to_owned(),
                depth,
            });
            return;
        }
        for edge in self.causal_edges_from(id) {
            self.visit(
                &edge.target,
                depth + 1,
                relation_name(&edge.relation),
                visited,
                trace,
            );
        }
        visited.remove(id);
    }

    fn causal_edges_from(&self, id: &str) -> Vec<&Edge> {
        self.edges
            .iter()
            .filter(|edge| {
                edge.source == id
                    && matches!(
                        edge.relation,
                        EdgeRelation::CausadoBy | EdgeRelation::WasDerivedFrom
                    )
                    && edge.target.starts_with("ADR-")
            })
            .collect()
    }

    pub fn cycle_ids(&self) -> Vec<String> {
        let mut ids = BTreeSet::new();
        for root in self.nodes.keys() {
            if let Some(trace) = self.trace(root) {
                for event in trace {
                    if let TraceEvent::Cycle { id, .. } = event {
                        ids.insert(id);
                    }
                }
            }
        }
        ids.into_iter().collect()
    }

    pub fn children(&self, root: &str) -> BTreeMap<String, Vec<String>> {
        let root = normalize_adr_id(root);
        let mut children: BTreeMap<String, BTreeSet<String>> = BTreeMap::from([
            ("causado_by".to_owned(), BTreeSet::new()),
            ("wasDerivedFrom".to_owned(), BTreeSet::new()),
            ("supersedes".to_owned(), BTreeSet::new()),
        ]);
        for edge in &self.edges {
            if edge.target != root {
                continue;
            }
            let key = match edge.relation {
                EdgeRelation::CausadoBy => "causado_by",
                EdgeRelation::WasDerivedFrom => "wasDerivedFrom",
                EdgeRelation::Supersedes => "supersedes",
                _ => continue,
            };
            children
                .get_mut(key)
                .expect("initialized relation")
                .insert(edge.source.clone());
        }
        children
            .into_iter()
            .map(|(key, values)| (key, values.into_iter().collect()))
            .collect()
    }
}

fn is_canonical_adr_id(value: &str) -> bool {
    let Some(number) = value.strip_prefix("ADR-") else {
        return false;
    };
    number.len() == 3 && number.chars().all(|character| character.is_ascii_digit())
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
