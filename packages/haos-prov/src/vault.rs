use std::collections::BTreeMap;

use crate::graph::CausalGraph;
use crate::model::ProvNode;

/// Parsed ADR collection with a reusable adjacency graph.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Vault {
    pub(crate) nodes: BTreeMap<String, ProvNode>,
}

impl Vault {
    pub(crate) fn from_nodes(nodes: BTreeMap<String, ProvNode>) -> Self {
        Self { nodes }
    }

    pub fn nodes(&self) -> &BTreeMap<String, ProvNode> {
        &self.nodes
    }

    pub fn graph(&self) -> CausalGraph {
        CausalGraph::new(self.nodes.clone())
    }
}
