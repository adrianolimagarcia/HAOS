use haos_prov::{
    check, desc, graph, parse_frontmatter, parse_vault, why, EdgeRelation, GraphIssue, ParseError,
    TraceEvent,
};

fn doc(id: &str, title: &str, causes: &str, derived: &str, body: &str) -> String {
    format!(
        "---\nid: {id}\ntitulo: {title}\nstatus: Aceito\ncausado_by: {causes}\nprov:\n  wasDerivedFrom: {derived}\n---\n{body}"
    )
}

#[test]
fn parses_only_frontmatter_and_ignores_body() {
    let source = "---\nid: ADR-1\ntitulo: Teste\nstatus: Aceito\ncausado_by: adr:ADR-2\nprov:\n  wasGeneratedBy: task:test\n---\ncausado_by: adr:ADR-999\n";
    let parsed = parse_frontmatter(source).expect("valid frontmatter");
    assert_eq!(parsed.frontmatter.id, "ADR-1");
    assert_eq!(parsed.frontmatter.causado_by, vec!["adr:ADR-2"]);
    assert!(parsed.body.contains("ADR-999"));
    assert!(!parsed
        .frontmatter
        .causado_by
        .contains(&"adr:ADR-999".to_owned()));
}

#[test]
fn rejects_invalid_yaml() {
    let error = parse_frontmatter("---\nid: [unterminated\n---\nbody").unwrap_err();
    assert!(matches!(error, ParseError::InvalidYaml(_)));
}

#[test]
fn empty_vault_is_valid_and_deterministic() {
    let vault = parse_vault(Vec::<(String, String)>::new()).expect("empty vault");
    let result = check(&vault);
    assert_eq!(result.node_count, 0);
    assert!(result.nodes.is_empty());
    assert!(result.ghosts.is_empty());
    assert!(result.cycles.is_empty());
}

#[test]
fn detects_ghost_references() {
    let vault = parse_vault(vec![(
        "a.md",
        doc("ADR-001", "A", "adr:ADR-999", "adr:ADR-999", "# body"),
    )])
    .expect("valid vault");
    let result = check(&vault);
    assert_eq!(result.ghosts.len(), 2);
    assert!(result.ghosts.iter().all(|issue| matches!(
        issue,
        GraphIssue::GhostReference {
            target,
            relation: EdgeRelation::CausadoBy | EdgeRelation::WasDerivedFrom,
            ..
        } if target == "ADR-999"
    )));
}

#[test]
fn detects_cycle_in_causal_trace() {
    let vault = parse_vault(vec![
        ("a.md", doc("ADR-001", "A", "adr:ADR-002", "", "")),
        ("b.md", doc("ADR-002", "B", "adr:ADR-001", "", "")),
    ])
    .expect("valid vault");
    let result = why(&vault, "ADR-001").expect("root exists");
    assert_eq!(result.cycle_ids, vec!["ADR-001"]);
    assert!(result
        .trace
        .iter()
        .any(|event| matches!(event, TraceEvent::Cycle { id, .. } if id == "ADR-001")));
    assert_eq!(check(&vault).cycles, vec!["ADR-001", "ADR-002"]);
}

#[test]
fn follows_causal_chain_and_descends_to_children() {
    let vault = parse_vault(vec![
        ("a.md", doc("ADR-001", "A", "", "", "")),
        (
            "b.md",
            doc("ADR-002", "B", "adr:ADR-001", "adr:ADR-001", ""),
        ),
        ("c.md", doc("ADR-003", "C", "adr:ADR-002", "", "")),
    ])
    .expect("valid vault");
    let trace = why(&vault, "ADR-003").expect("root exists");
    let ids: Vec<_> = trace
        .trace
        .iter()
        .filter_map(|event| match event {
            TraceEvent::Node { id, .. } => Some(id.as_str()),
            _ => None,
        })
        .collect();
    assert_eq!(ids, vec!["ADR-003", "ADR-002", "ADR-001", "ADR-001"]);
    let descendants = desc(&vault, "ADR-001").expect("root exists");
    assert_eq!(descendants.children["causado_by"], vec!["ADR-002"]);
    assert_eq!(descendants.children["wasDerivedFrom"], vec!["ADR-002"]);
    assert_eq!(graph(&vault).nodes, vec!["ADR-001", "ADR-002", "ADR-003"]);
}
