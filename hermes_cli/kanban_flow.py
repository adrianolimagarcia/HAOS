"""Kanban Flow Engine: Declarative DAGs, MoA Evaluator and Delta Passing.

Extends the HAOS multi-agent orchestration capabilities without adding external dependencies:
1. Declarative Flow DSL (compact syntax: 'scout -> (worker_a, worker_b) -> judge' and rich dict/YAML).
2. Topological DAG compiler with cycle detection and atomic Kanban task provisioning.
3. Delta Passing context manager: strictly transfers executive summaries, structured findings
   and artifact references (never flooding parent/sibling context with raw transcripts).
4. Mixture of Agents (MoA) Evaluator: parallel multi-expert review with deterministic Judge rubrics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_swarm import BLACKBOARD_PREFIX, latest_blackboard, post_blackboard_update

FLOW_BLACKBOARD_PREFIX = "[flow:blackboard] "


@dataclass(frozen=True)
class FlowNode:
    """A single node specification in a flow graph."""

    id: str
    profile: str
    title: str
    description: str = ""
    skills: list[str] = field(default_factory=list)
    output_schema: Optional[dict[str, Any]] = None
    priority: int = 0
    max_runtime_seconds: Optional[int] = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FlowEdge:
    """Directed dependency edge: source must complete before target can run."""

    source: str
    target: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FlowGraph:
    """A directed acyclic graph (DAG) of agent flow nodes."""

    name: str
    goal: str
    nodes: dict[str, FlowNode] = field(default_factory=dict)
    edges: list[FlowEdge] = field(default_factory=list)

    def add_node(self, node: FlowNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"Duplicate node ID in flow: {node.id!r}")
        self.nodes[node.id] = node

    def add_edge(self, source: str, target: str) -> None:
        if source not in self.nodes:
            raise ValueError(f"Edge source node {source!r} does not exist in flow")
        if target not in self.nodes:
            raise ValueError(f"Edge target node {target!r} does not exist in flow")
        edge = FlowEdge(source=source, target=target)
        if edge not in self.edges:
            self.edges.append(edge)

    def parent_map(self) -> dict[str, list[str]]:
        """Map of node_id -> list of immediate parent (source) node IDs."""
        parents: dict[str, list[str]] = {nid: [] for nid in self.nodes}
        for edge in self.edges:
            parents[edge.target].append(edge.source)
        return parents

    def child_map(self) -> dict[str, list[str]]:
        """Map of node_id -> list of immediate child (target) node IDs."""
        children: dict[str, list[str]] = {nid: [] for nid in self.nodes}
        for edge in self.edges:
            children[edge.source].append(edge.target)
        return children

    def entry_nodes(self) -> list[str]:
        """Nodes with zero in-degrees (no parents)."""
        pm = self.parent_map()
        return [nid for nid, p in pm.items() if not p]

    def terminal_nodes(self) -> list[str]:
        """Nodes with zero out-degrees (no children)."""
        cm = self.child_map()
        return [nid for nid, c in cm.items() if not c]

    def validate_dag(self) -> list[str]:
        """Validates that the graph is a DAG (no cycles).

        Returns:
            Topologically sorted list of node IDs.

        Raises:
            ValueError: If a cycle is detected or graph is empty.
        """
        if not self.nodes:
            raise ValueError("Flow graph contains no nodes")

        in_degrees: dict[str, int] = {nid: 0 for nid in self.nodes}
        adjacency: dict[str, list[str]] = {nid: [] for nid in self.nodes}

        for edge in self.edges:
            adjacency[edge.source].append(edge.target)
            in_degrees[edge.target] += 1

        queue = [nid for nid, deg in in_degrees.items() if deg == 0]
        sorted_nodes: list[str] = []

        while queue:
            curr = queue.pop(0)
            sorted_nodes.append(curr)
            for neighbor in adjacency[curr]:
                in_degrees[neighbor] -= 1
                if in_degrees[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_nodes) != len(self.nodes):
            remaining = [nid for nid, deg in in_degrees.items() if deg > 0]
            raise ValueError(f"Flow graph contains a cycle involving nodes: {remaining}")

        return sorted_nodes

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "goal": self.goal,
            "nodes": {nid: n.as_dict() for nid, n in self.nodes.items()},
            "edges": [e.as_dict() for e in self.edges],
            "topological_order": self.validate_dag(),
        }


# ---------------------------------------------------------------------------
# DSL Parser
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\s*->\s*")
_STAGE_ITEM_RE = re.compile(r"^\((.*)\)$")


def _parse_node_token(token: str) -> FlowNode:
    """Parse node token: 'id[:profile[:title]]'."""
    raw = token.strip()
    if not raw:
        raise ValueError("Empty node identifier in flow expression")

    parts = [p.strip() for p in raw.split(":", 2)]
    node_id = parts[0]
    if not re.match(r"^[a-zA-Z0-9_\-]+$", node_id):
        raise ValueError(f"Invalid node ID format: {node_id!r} (must be alphanumeric/dash/underscore)")

    profile = parts[1] if len(parts) >= 2 and parts[1] else node_id
    title = parts[2] if len(parts) == 3 and parts[2] else f"Flow step: {node_id}"

    return FlowNode(id=node_id, profile=profile, title=title)


def parse_flow_dsl(expression: str, *, goal: str = "", name: str = "custom_flow") -> FlowGraph:
    """Parse a compact pipeline expression into a FlowGraph.

    Examples:
        'scout -> (researcher_a, researcher_b) -> synthesizer'
        'scout:researcher:Map surface -> (sec:security:Audit, perf:perf:Bench) -> judge:conductor:Decide'
    """
    clean_expr = expression.strip()
    if not clean_expr:
        raise ValueError("Flow expression cannot be empty")

    stages_raw = _TOKEN_RE.split(clean_expr)
    if not stages_raw:
        raise ValueError("Invalid flow expression syntax")

    graph = FlowGraph(name=name, goal=goal)
    stage_node_ids: list[list[str]] = []

    for stage_idx, stage_str in enumerate(stages_raw):
        stage_str = stage_str.strip()
        if not stage_str:
            raise ValueError(f"Empty stage at position {stage_idx + 1}")

        group_match = _STAGE_ITEM_RE.match(stage_str)
        if group_match:
            # Parallel group: (a, b, c)
            inner = group_match.group(1).strip()
            if not inner:
                raise ValueError(f"Empty parallel group in stage {stage_idx + 1}")
            items = [item.strip() for item in inner.split(",") if item.strip()]
            if not items:
                raise ValueError(f"No valid nodes in parallel group in stage {stage_idx + 1}")
            current_stage_ids = []
            for item in items:
                node = _parse_node_token(item)
                graph.add_node(node)
                current_stage_ids.append(node.id)
            stage_node_ids.append(current_stage_ids)
        else:
            # Single node
            node = _parse_node_token(stage_str)
            graph.add_node(node)
            stage_node_ids.append([node.id])

    # Connect consecutive stages: every node in stage N points to every node in stage N+1
    for i in range(len(stage_node_ids) - 1):
        sources = stage_node_ids[i]
        targets = stage_node_ids[i + 1]
        for src in sources:
            for tgt in targets:
                graph.add_edge(src, tgt)

    graph.validate_dag()
    return graph


def parse_flow_dict(data: dict[str, Any]) -> FlowGraph:
    """Build a FlowGraph from a rich dict/JSON/YAML structure."""
    name = str(data.get("name", "flow")).strip()
    goal = str(data.get("goal", "")).strip()
    graph = FlowGraph(name=name, goal=goal)

    nodes_raw = data.get("nodes", {})
    if isinstance(nodes_raw, dict):
        for nid, spec in nodes_raw.items():
            if isinstance(spec, dict):
                graph.add_node(
                    FlowNode(
                        id=str(nid),
                        profile=str(spec.get("profile", nid)),
                        title=str(spec.get("title", f"Step: {nid}")),
                        description=str(spec.get("description", spec.get("task", ""))),
                        skills=list(spec.get("skills", [])),
                        output_schema=spec.get("output_schema"),
                        priority=int(spec.get("priority", 0)),
                        max_runtime_seconds=spec.get("max_runtime_seconds"),
                    )
                )
            else:
                graph.add_node(FlowNode(id=str(nid), profile=str(nid), title=str(spec)))
    elif isinstance(nodes_raw, list):
        for spec in nodes_raw:
            if isinstance(spec, dict) and "id" in spec:
                graph.add_node(
                    FlowNode(
                        id=str(spec["id"]),
                        profile=str(spec.get("profile", spec["id"])),
                        title=str(spec.get("title", f"Step: {spec['id']}")),
                        description=str(spec.get("description", spec.get("task", ""))),
                        skills=list(spec.get("skills", [])),
                        output_schema=spec.get("output_schema"),
                        priority=int(spec.get("priority", 0)),
                        max_runtime_seconds=spec.get("max_runtime_seconds"),
                    )
                )

    edges_raw = data.get("edges", [])
    for edge_spec in edges_raw:
        if isinstance(edge_spec, str):
            # 'a -> b' or 'a -> (b, c)'
            sub_graph = parse_flow_dsl(edge_spec)
            for sub_edge in sub_graph.edges:
                graph.add_edge(sub_edge.source, sub_edge.target)
        elif isinstance(edge_spec, dict) and "source" in edge_spec and "target" in edge_spec:
            graph.add_edge(str(edge_spec["source"]), str(edge_spec["target"]))
        elif isinstance(edge_spec, (list, tuple)) and len(edge_spec) == 2:
            graph.add_edge(str(edge_spec[0]), str(edge_spec[1]))

    graph.validate_dag()
    return graph


# ---------------------------------------------------------------------------
# Delta Context Manager & Contracts (Delta Passing)
# ---------------------------------------------------------------------------

AGENT_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "failed", "blocked"]},
        "summary": {"type": "string", "description": "Concise summary of findings (max 400 chars)"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "info"]},
                    "location": {"type": "string"},
                    "issue": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["severity", "issue"],
            },
        },
        "artifacts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "File paths of produced artifacts on disk",
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "blockers": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "summary", "confidence"],
}


class DeltaContextManager:
    """Manages lean context transfer across flow DAG stages.

    Instead of passing whole transcripts or conversation histories, this manager
    compiles discrete deltas:
    - Predecessor executive summaries.
    - Path references to on-disk artifacts.
    - Machine-readable structured findings.
    """

    @staticmethod
    def format_predecessor_delta(
        parent_results: dict[str, dict[str, Any]],
        *,
        max_summary_len: int = 500,
    ) -> str:
        """Format lean markdown summary of predecessor results for a downstream node."""
        if not parent_results:
            return ""

        lines = ["\n### Delta Context from Predecessors"]
        for parent_id, res in sorted(parent_results.items()):
            status = res.get("status", "unknown")
            conf = res.get("confidence")
            conf_str = f", confidence: {conf:.2f}" if isinstance(conf, (int, float)) else ""
            lines.append(f"#### Node '{parent_id}' [{status}{conf_str}]")

            summary = str(res.get("summary", "")).strip()
            if summary:
                if len(summary) > max_summary_len:
                    summary = summary[:max_summary_len] + "..."
                lines.append(f"- **Summary**: {summary}")

            artifacts = res.get("artifacts", [])
            if artifacts and isinstance(artifacts, list):
                lines.append("- **Artifacts Produced**:")
                for art in artifacts:
                    lines.append(f"  * `{art}`")

            findings = res.get("findings", [])
            if findings and isinstance(findings, list):
                lines.append("- **Key Findings**:")
                for f in findings[:5]:
                    if isinstance(f, dict):
                        sev = f.get("severity", "info").upper()
                        issue = f.get("issue", "")
                        loc = f.get("location", "")
                        loc_s = f" at {loc}" if loc else ""
                        lines.append(f"  * [{sev}] {issue}{loc_s}")

            blockers = res.get("blockers", [])
            if blockers and isinstance(blockers, list):
                lines.append("- **Blockers Identified**:")
                for b in blockers:
                    lines.append(f"  * ⚠ {b}")

        return "\n".join(lines) + "\n"

    @staticmethod
    def build_node_task_body(
        node: FlowNode,
        *,
        goal: str,
        root_id: str,
        parent_node_ids: list[str],
        predecessor_results: Optional[dict[str, dict[str, Any]]] = None,
    ) -> str:
        """Compose the definitive task body for a flow node card."""
        desc = (node.description or node.title).strip()
        delta_str = ""
        if predecessor_results:
            delta_str = DeltaContextManager.format_predecessor_delta(predecessor_results)

        body_parts = [
            f"## Goal\n{goal.strip()}\n",
            f"## Task for Step '{node.id}'\n{desc}\n",
        ]
        if delta_str:
            body_parts.append(delta_str)

        body_parts.append(
            f"## Flow Invariants & Protocol\n"
            f"- Flow Root Task / Shared Blackboard: `{root_id}`\n"
            f"- Direct Predecessors: {', '.join(f'`{p}`' for p in parent_node_ids) if parent_node_ids else 'None (entry step)'}\n"
            f"- **Delta Passing Rule**: Do NOT dump raw execution logs. Return a concise summary, list any generated "
            f"artifact file paths, and store structured metadata on task completion.\n"
        )
        return "\n".join(body_parts)


# ---------------------------------------------------------------------------
# Mixture of Agents (MoA) Evaluator
# ---------------------------------------------------------------------------

MOA_SPECIALIST_ROLES: dict[str, dict[str, Any]] = {
    "security": {
        "profile": "security-reviewer",
        "title": "Security & Threat Model Audit",
        "description": (
            "Analyze the target for vulnerabilities, privilege escalation, secret leakage, "
            "untrusted input handling, and security invariant violations. Produce concrete severity-rated findings."
        ),
        "skills": ["cyber-audit"],
    },
    "architecture": {
        "profile": "architecture-reviewer",
        "title": "Architecture & Invariants Review",
        "description": (
            "Evaluate structural integrity: narrow-waist adherence, modularity, avoidance of god-files, "
            "caching preservation, idempotency, and HAOS platform standards."
        ),
        "skills": [],
    },
    "performance": {
        "profile": "performance-reviewer",
        "title": "Performance & Resource Efficiency Review",
        "description": (
            "Evaluate latency, memory footprint, lock contention, token budget explosion, "
            "and algorithmic complexity. Identify potential bottlenecks and resource leaks."
        ),
        "skills": [],
    },
    "adversarial": {
        "profile": "adversarial-reviewer",
        "title": "Adversarial Edge-Case Audit",
        "description": (
            "Actively probe for edge cases: network failure, partial state, rollback failure, race conditions, "
            "malformed inputs, and poison pills. Document exact failure modes."
        ),
        "skills": [],
    },
}

JUDGE_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["approve", "reject", "changes_requested"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "score": {"type": "number", "minimum": 0.0, "maximum": 100.0},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "required_actions": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["decision", "confidence", "summary"],
}


def build_moa_flow(
    *,
    goal: str,
    target_description: str,
    specialists: Optional[list[str]] = None,
    judge_profile: str = "conductor",
    name: str = "moa_evaluation",
) -> FlowGraph:
    """Build a deterministic Mixture of Agents (MoA) evaluation graph.

    Topological shape:
        scout_setup -> (specialist_1, specialist_2, specialist_k) -> judge
    """
    if specialists is None:
        specialists = ["security", "architecture", "performance"]

    graph = FlowGraph(name=name, goal=goal)

    # Setup node: inspect and prepare target artifacts
    setup_node = FlowNode(
        id="prepare_target",
        profile="researcher",
        title="Inspect & Prepare Target for Evaluation",
        description=f"Inspect target and prepare verifiable diff/artifact references for specialists:\n{target_description}",
    )
    graph.add_node(setup_node)

    specialist_ids: list[str] = []
    for role_name in specialists:
        role_info = MOA_SPECIALIST_ROLES.get(
            role_name,
            {
                "profile": f"{role_name}-reviewer",
                "title": f"{role_name.capitalize()} Review",
                "description": f"Perform rigorous review with focus on {role_name}.",
                "skills": [],
            },
        )
        spec_id = f"eval_{role_name}"
        node = FlowNode(
            id=spec_id,
            profile=role_info["profile"],
            title=role_info["title"],
            description=f"{role_info['description']}\n\nTarget:\n{target_description}",
            skills=role_info.get("skills", []),
            output_schema=AGENT_RESULT_SCHEMA,
        )
        graph.add_node(node)
        graph.add_edge(setup_node.id, spec_id)
        specialist_ids.append(spec_id)

    # Judge node: synthesizes all evaluations and makes deterministically reasoned verdict
    judge_node = FlowNode(
        id="judge",
        profile=judge_profile,
        title="MoA Conductor Judge Verdict",
        description=(
            "Review specialist reports from security, architecture, performance, and edge cases. "
            "Determine final decision ('approve', 'reject', or 'changes_requested'). "
            "Provide weighted risk evaluation and required remediations."
        ),
        skills=["requesting-code-review"],
        output_schema=JUDGE_DECISION_SCHEMA,
    )
    graph.add_node(judge_node)
    for spec_id in specialist_ids:
        graph.add_edge(spec_id, judge_node.id)

    graph.validate_dag()
    return graph


def synthesize_moa_judge_verdict(specialist_reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Deterministic offline synthesis of multiple specialist evaluations.

    Rubric:
    - If ANY specialist reports a critical finding or blocker, outcome cannot be 'approve'.
    - Confidence is weighted average of individual specialist confidences.
    """
    all_findings: list[dict[str, Any]] = []
    all_blockers: list[str] = []
    confidences: list[float] = []

    for role, report in specialist_reports.items():
        conf = report.get("confidence", 0.8)
        if isinstance(conf, (int, float)):
            confidences.append(float(conf))

        for finding in report.get("findings", []):
            if isinstance(finding, dict):
                f = dict(finding)
                f["reported_by"] = role
                all_findings.append(f)
                if f.get("severity") in ("critical", "high"):
                    all_blockers.append(f"[{role.upper()}] {f.get('issue', 'Critical issue')}")

        for b in report.get("blockers", []):
            all_blockers.append(f"[{role.upper()}] {b}")

    avg_conf = sum(confidences) / len(confidences) if confidences else 0.85

    if any(f.get("severity") == "critical" for f in all_findings) or len(all_blockers) >= 3:
        decision = "reject"
    elif all_blockers:
        decision = "changes_requested"
    else:
        decision = "approve"

    return {
        "decision": decision,
        "confidence": round(avg_conf, 2),
        "blockers": all_blockers,
        "findings_count": len(all_findings),
        "specialists_evaluated": list(specialist_reports.keys()),
        "summary": f"MoA Judge verdict: {decision.upper()} with {len(all_blockers)} blocking issues across {len(specialist_reports)} specialists.",
    }


# ---------------------------------------------------------------------------
# Kanban Task Provisioning Engine
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FlowCreated:
    """IDs and mapping generated when a flow DAG is committed to Kanban."""

    root_id: str
    flow_name: str
    task_mapping: dict[str, str]  # node_id -> task_id
    entry_task_ids: list[str]
    terminal_task_ids: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "root_id": self.root_id,
            "flow_name": self.flow_name,
            "task_mapping": self.task_mapping,
            "entry_task_ids": self.entry_task_ids,
            "terminal_task_ids": self.terminal_task_ids,
        }


def _activate_flow_root(
    conn: sqlite3.Connection,
    root_id: str,
    *,
    summary: str,
    metadata: dict[str, Any],
) -> bool:
    """Inline activation of the flow root card inside write_txn."""
    cur = conn.execute(
        """
        UPDATE tasks
           SET status        = 'done',
               completed_at  = ?,
               claim_lock    = NULL,
               claim_expires = NULL,
               worker_pid    = NULL
         WHERE id = ?
           AND status = 'blocked'
        """,
        (int(time.time()), root_id),
    )
    if cur.rowcount != 1:
        return False
    run_id = kb._synthesize_ended_run(conn, root_id, outcome="completed", summary=summary, metadata=metadata)
    kb._append_event(
        conn,
        root_id,
        "completed",
        {"result_len": 0, "summary": summary[:400] or None},
        run_id=run_id,
    )
    return True


def create_kanban_flow(
    conn: sqlite3.Connection,
    *,
    graph: FlowGraph,
    goal: Optional[str] = None,
    root_title: Optional[str] = None,
    created_by: str = "flow-orchestrator",
    tenant: Optional[str] = None,
    priority: int = 0,
    workspace_kind: Optional[str] = None,
    workspace_path: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> FlowCreated:
    """Atomically compile and persist a FlowGraph DAG into the Kanban database."""
    topological_order = graph.validate_dag()
    eff_goal = (goal or graph.goal or "Execute flow workflow").strip()

    activation_summary = f"Flow DAG '{graph.name}' compiled with {len(graph.nodes)} nodes."

    with kb.write_txn(conn):
        # 1. Create root blackboard task
        root_id = kb.create_task(
            conn,
            title=root_title or f"Flow [{graph.name}]: {eff_goal.splitlines()[0][:70]}",
            body=(
                f"Flow Engine DAG root card for workflow '{graph.name}'.\n"
                f"Serves as the shared blackboard, state anchor and audit log.\n\n"
                f"Goal:\n{eff_goal}\n"
            ),
            assignee=created_by,
            priority=priority,
            idempotency_key=idempotency_key,
            initial_status="blocked",
            created_by=created_by,
            tenant=tenant,
            workspace_kind=workspace_kind,
            workspace_path=workspace_path,
        )

        # Idempotency recovery check
        bb = latest_blackboard(conn, root_id)
        existing_flow = bb.get("flow_topology")
        if isinstance(existing_flow, dict) and "task_mapping" in existing_flow:
            mapping = existing_flow["task_mapping"]
            entries = existing_flow.get("entry_task_ids", [])
            terminals = existing_flow.get("terminal_task_ids", [])
            return FlowCreated(root_id, graph.name, mapping, entries, terminals)

        # 2. Instantiate tasks per node in topological order
        parent_map = graph.parent_map()
        task_mapping: dict[str, str] = {}

        for nid in topological_order:
            node = graph.nodes[nid]
            parent_node_ids = parent_map.get(nid, [])

            # Parents in Kanban: if no predecessors, depends on root; else depends on mapped parent tasks
            if not parent_node_ids:
                kanban_parents = [root_id]
            else:
                kanban_parents = [task_mapping[p] for p in parent_node_ids]

            task_body = DeltaContextManager.build_node_task_body(
                node,
                goal=eff_goal,
                root_id=root_id,
                parent_node_ids=parent_node_ids,
            )

            tid = kb.create_task(
                conn,
                title=f"[{graph.name}:{node.id}] {node.title}",
                body=task_body,
                assignee=node.profile,
                parents=kanban_parents,
                priority=node.priority or priority,
                skills=node.skills or None,
                max_runtime_seconds=node.max_runtime_seconds,
                created_by=created_by,
                tenant=tenant,
                workspace_kind=workspace_kind,
                workspace_path=workspace_path,
            )
            task_mapping[nid] = tid

        # 3. Post topology update to blackboard
        entry_task_ids = [task_mapping[nid] for nid in graph.entry_nodes()]
        terminal_task_ids = [task_mapping[nid] for nid in graph.terminal_nodes()]
        flow_created = FlowCreated(root_id, graph.name, task_mapping, entry_task_ids, terminal_task_ids)

        post_blackboard_update(
            conn,
            root_id,
            author=created_by,
            key="flow_topology",
            value=flow_created.as_dict() | {"edges": [e.as_dict() for e in graph.edges]},
        )

        # 4. Activate root task
        if not _activate_flow_root(
            conn,
            root_id,
            summary=activation_summary,
            metadata={
                "kind": "kanban_flow_v1",
                "flow_name": graph.name,
                "node_count": len(graph.nodes),
            },
        ):
            raise RuntimeError("Could not activate flow root task")

    # 5. Outside transaction: recompute ready tasks so entry nodes become ready
    kb.recompute_ready(conn)
    root_task = kb.get_task(conn, root_id)
    latest_run = kb.latest_run(conn, root_id)
    kb._fire_kanban_lifecycle_hook(
        "kanban_task_completed",
        root_id,
        board=kb.get_current_board(),
        assignee=root_task.assignee if root_task else None,
        run_id=latest_run.id if latest_run else None,
        summary=activation_summary,
    )

    return flow_created
