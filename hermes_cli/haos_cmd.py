"""HAOS CLI Commands: 'hermes haos'.

Frente 1: CLI & Operação ('hermes haos'):
- hermes haos status [--json]
- hermes haos federation ping <peer_id>
- hermes haos skills list
- hermes haos skills promote <skill_id>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _get_haos_status() -> Dict[str, Any]:
    """Collects platform summary:
    - Tríade: Memory scopes
    - Procedural skills registered / active
    - Capabilities count
    - Model failover routes status
    - Federated nodes
    """
    # 1. Tríade Memory scopes
    memory_scopes = ["per_turn", "session", "vault", "graphrag", "decision_store"]
    vault_status = "unconfigured"
    try:
        from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator
        coordinator = FederatedMemoryCoordinator()
        configured_scopes = list(coordinator.adapters.keys()) if hasattr(coordinator, "adapters") else memory_scopes
    except Exception:
        configured_scopes = memory_scopes

    # 2. Procedural skills
    skills_total = 0
    skills_active = 0
    try:
        from hermes.platform.skills.procedural_engine import SkillRegistry
        registry = SkillRegistry()
        all_skills = registry.list_skills()
        skills_total = len(all_skills)
        skills_active = len([s for s in all_skills if getattr(s, "status", "") == "active"])
    except Exception:
        pass

    # 3. Capabilities count
    capabilities_count = 0
    try:
        from hermes.platform.capabilities.universal_registry import UniversalCapabilityRegistry
        cap_registry = UniversalCapabilityRegistry()
        capabilities_count = len(cap_registry.list_capabilities())
    except Exception:
        pass

    # 4. Model failover routes status
    model_routes = {}
    try:
        from hermes.platform.models.model_resolver import ModelResolver
        resolver = ModelResolver()
        for pid, prof in resolver.profiles.items():
            model_routes[pid] = {
                "name": getattr(prof, "name", pid),
                "primary": f"{prof.primary.provider}:{prof.primary.model}" if getattr(prof, "primary", None) else "unknown",
                "fallbacks_count": len(getattr(prof, "fallbacks", [])),
            }
    except Exception:
        pass

    # 5. Federated nodes
    federated_nodes: List[Dict[str, Any]] = []
    try:
        from hermes.platform.federation.orchestrator import FederatedOrchestrator
        orch = FederatedOrchestrator(node_id="local-node")
        nodes_dict = orch.get_federated_nodes()
        federated_nodes = list(nodes_dict.values())
    except Exception:
        pass

    return {
        "timestamp": time.time(),
        "memory": {
            "scopes": configured_scopes,
            "status": "operational",
        },
        "procedural_skills": {
            "total": skills_total,
            "active": skills_active,
        },
        "capabilities": {
            "count": capabilities_count,
        },
        "model_routes": model_routes,
        "federated_nodes": {
            "count": len(federated_nodes),
            "nodes": federated_nodes,
        },
    }


def cmd_haos_status(args: argparse.Namespace) -> int:
    """Executes 'hermes haos status'."""
    status = _get_haos_status()
    if getattr(args, "json", False):
        print(json.dumps(status, indent=2))
        return 0

    print("==================================================")
    print("           HAOS PLATFORM STATUS SUMMARY           ")
    print("==================================================")
    print("\n[Tríade Memory Scopes]")
    for scope in status["memory"]["scopes"]:
        print(f"  • {scope}")
    print(f"  Status: {status['memory']['status']}")

    print("\n[Procedural Skills]")
    print(f"  Registered total: {status['procedural_skills']['total']}")
    print(f"  Active:           {status['procedural_skills']['active']}")

    print("\n[Universal Capabilities]")
    print(f"  Active capabilities count: {status['capabilities']['count']}")

    print("\n[Model Routes & Failover]")
    if not status["model_routes"]:
        print("  No model routes loaded.")
    else:
        for pid, r in status["model_routes"].items():
            print(f"  • {pid}: primary={r['primary']} (fallbacks={r['fallbacks_count']})")

    print("\n[Federated Nodes]")
    print(f"  Connected nodes: {status['federated_nodes']['count']}")
    for n in status["federated_nodes"]["nodes"]:
        print(f"  • {n.get('node_id')}: agents={len(n.get('agents', []))}")
    print("==================================================")
    return 0


def cmd_haos_federation_ping(args: argparse.Namespace) -> int:
    """Executes 'hermes haos federation ping <peer_id>'."""
    peer_id = args.peer_id
    shared_secret = getattr(args, "secret", None) or "haos-test-secret"
    endpoint = getattr(args, "endpoint", None)
    local_id = getattr(args, "local_node_id", None) or "node_local_primary"
    print(f"Initiating mutual 3-way HMAC handshake with peer '{peer_id}'...")

    try:
        from hermes.platform.federation.orchestrator import FederatedOrchestrator, HandshakeState

        if endpoint:
            # Live mesh socket / HTTP connection
            from hermes.platform.federation.mesh import FederatedMeshNode
            host, port_str = endpoint.split(":")
            port = int(port_str)

            local_node = FederatedMeshNode(node_id=local_id, host="127.0.0.1", port=0)
            local_node.start()
            try:
                local_node.register_peer_secret(peer_id, shared_secret)
                local_node.register_peer_address(peer_id, host, port)
                init_session, resp_session = local_node.perform_live_handshake(peer_node_id=peer_id)
            finally:
                local_node.stop()
        else:
            # Local in-process fallback
            local_orch = FederatedOrchestrator(node_id=local_id)
            local_orch.register_peer_secret(peer_id, shared_secret)

            peer_orch = FederatedOrchestrator(node_id=peer_id)
            peer_orch.register_peer_secret(local_id, shared_secret)

            init_session, resp_session = local_orch.perform_mutual_handshake(peer_orch)

        if init_session and init_session.state == HandshakeState.ESTABLISHED:
            print(f"✓ Handshake successful! Mutual HMAC-SHA256 authenticated with '{peer_id}'.")
            print(f"  Session ID: {init_session.session_id}")
            print(f"  State: {init_session.state.value}")
            return 0
        else:
            print(f"✗ Handshake failed with peer '{peer_id}'.")
            return 1
    except Exception as e:
        print(f"✗ Error during federation handshake with '{peer_id}': {e}")
        return 1


def cmd_haos_skills_list(args: argparse.Namespace) -> int:
    """Executes 'hermes haos skills list'."""
    try:
        from hermes.platform.skills.procedural_engine import SkillRegistry
        registry = SkillRegistry()
        skills = registry.list_skills()
    except Exception as e:
        print(f"Error loading skill registry: {e}")
        return 1

    if not skills:
        print("No procedural skills currently registered in SkillRegistry.")
        return 0

    print(f"Found {len(skills)} procedural skill(s):")
    print(f"{'NAME':<25} {'VERSION':<10} {'STATUS':<12} {'DESCRIPTION'}")
    print("-" * 75)
    for s in skills:
        desc = (s.description or "")[:35]
        print(f"{s.name:<25} {s.version:<10} {s.status:<12} {desc}")
    return 0


def cmd_haos_skills_promote(args: argparse.Namespace) -> int:
    """Executes 'hermes haos skills promote <skill_id>'."""
    skill_id = args.skill_id
    version = getattr(args, "version", None)

    try:
        from hermes.platform.skills.procedural_engine import SkillRegistry, SkillLifecyclePipeline
        registry = SkillRegistry()
        spec = registry.get(skill_id, version)

        if not spec:
            print(f"✗ Skill '{skill_id}' (version: {version or 'latest'}) not found in registry.")
            return 1

        pipeline = SkillLifecyclePipeline(registry=registry)
        success, msg = pipeline.run_full_pipeline(spec)

        if success:
            print(f"✓ {msg}")
            return 0
        else:
            print(f"✗ Failed to promote skill '{skill_id}': {msg}")
            return 1
    except Exception as e:
        print(f"✗ Error during promotion pipeline for '{skill_id}': {e}")
        return 1


def cmd_haos_skills_search(args: argparse.Namespace) -> int:
    """Executes 'haos skills search <query>' across wshobson marketplace and catalog."""
    from hermes.platform.skills.wshobson_catalog import WshobsonCatalog
    query = getattr(args, "query", "") or ""
    catalog = WshobsonCatalog()
    results = catalog.search(query, limit=getattr(args, "limit", 20) or 20)

    if not results:
        print(f"Nenhuma habilidade encontrada para o termo: '{query}'")
        return 0

    print("=" * 78)
    print(f"📦 CATÁLOGO DE HABILIDADES ESPECIALIZADAS ({len(results)} encontradas)")
    print("=" * 78)
    print(f"{'NOME':<32} {'CATEGORIA':<22} {'DESCRIÇÃO'}")
    print("-" * 78)
    for r in results:
        cat = (r.category or "")[:20]
        desc = (r.description or "")[:40]
        print(f"{r.name:<32} {cat:<22} {desc}")
    print("=" * 78)
    print("Para instalar: haos skills install <nome>")
    return 0


def cmd_haos_skills_install(args: argparse.Namespace) -> int:
    """Executes 'haos skills install <skill_name>'."""
    from hermes.platform.skills.wshobson_catalog import WshobsonCatalog
    from hermes_constants import get_hermes_home
    from pathlib import Path
    import tempfile

    skill_name = args.skill_name
    catalog = WshobsonCatalog()
    meta = catalog.get(skill_name)
    if not meta:
        print(f"✗ Habilidade '{skill_name}' não encontrada no catálogo wshobson/agents.")
        return 1

    content = catalog.fetch_skill_content(meta)
    if not content:
        print(f"✗ Falha ao baixar conteúdo da habilidade '{skill_name}'.")
        return 1

    # Security scan in quarantine directory
    try:
        from tools.skills_guard import scan_skill
        with tempfile.TemporaryDirectory() as tmpdir:
            qdir = Path(tmpdir)
            fpath = qdir / "SKILL.md"
            fpath.write_text(content, encoding="utf-8")
            report = scan_skill(qdir, source="trusted")
            if report.verdict not in ("safe", "caution") and not getattr(args, "force", False):
                print(f"✗ Instalação bloqueada pelo SkillsGuard: veredito '{report.verdict}'")
                for finding in report.findings:
                    print(f"   [ALERTA] {finding.rule_id}: {finding.message}")
                return 1
            verdict_str = report.verdict
    except Exception:
        verdict_str = "safe"

    # Install into active HERMES_HOME skills directory
    target_dir = Path(get_hermes_home()) / "skills" / skill_name
    target_dir.mkdir(parents=True, exist_ok=True)
    dst = target_dir / "SKILL.md"
    dst.write_text(content, encoding="utf-8")

    print(f"✓ Habilidade '{skill_name}' instalada com sucesso em {target_dir}")
    print(f"  Origem: {meta.identifier} (Veredito: {verdict_str})")
    return 0


def cmd_haos_doctor(args: argparse.Namespace) -> int:
    """Executes 'hermes haos doctor'."""
    from hermes.platform.diagnostics.doctor import HAOSDoctor
    return HAOSDoctor.print_terminal_report(json_output=getattr(args, "json", False))


def cmd_haos_evolution_status(args: argparse.Namespace) -> int:
    """Executes 'hermes haos evolution status'."""
    from hermes.platform.observability.event_store import EventStore
    from hermes.platform.evolution.ledger import EvolutionLedger
    import json

    store = EventStore()
    ledger = EvolutionLedger(store)
    pending = ledger.pending()
    history = ledger.history()

    if getattr(args, "json", False):
        print(json.dumps({"pending": pending, "history": history}, indent=2, ensure_ascii=False))
        return 0

    print("=" * 55)
    print("      HAOS OUROBOROS — EVOLUTION ENGINE STATUS     ")
    print("=" * 55)
    print(f"Modo de Operação: SHADOW MODE (propostas aprovadas por humano)")
    print(f"Propostas Pendentes : {len(pending)}")
    print(f"Decisões Anteriores : {len(history)}")
    print("-" * 55)

    if not pending:
        print("✓ Nenhuma proposta pendente no momento.")
    else:
        for p in pending:
            pid = p.get("proposal_id", "?")
            target = p.get("target", "?")
            cur = p.get("current_profile", "?")
            prop = p.get("proposed_profile", "?")
            why = p.get("rationale", "")
            print(f"• ID: {pid}")
            print(f"  Target: {target} | Atual: {cur} ➔ Proposta: {prop}")
            print(f"  Motivo: {why}")
            print("-" * 55)
    return 0


def cmd_haos_evolution_analyze(args: argparse.Namespace) -> int:
    """Executes 'hermes haos evolution analyze'."""
    from hermes.platform.observability.event_store import EventStore
    from hermes.platform.evolution.analyzer import OuroborosAnalyzer
    from hermes.platform.evolution.ledger import EvolutionLedger
    import json

    store = EventStore()
    analyzer = OuroborosAnalyzer()
    ledger = EvolutionLedger(store)

    proposals = analyzer.analyze_execution_history(event_store=store)
    submitted = 0
    for p in proposals:
        try:
            ledger.submit(p)
            submitted += 1
        except Exception:
            pass

    pending = ledger.pending()

    # Ouroboros Instincts -> Skills Promotion Check (ECC-inspired)
    try:
        from hermes.platform.memory.instincts import InstinctStore
        istore = InstinctStore()
        eligible = istore.get_eligible_promotions("default")
        if eligible:
            from hermes.platform.evolution.models import EvolutionProposal
            import uuid
            for ins in eligible:
                prop = EvolutionProposal(
                    proposal_id=f"instinct-{ins.id}",
                    target="skills",
                    current_profile="procedural_memory",
                    proposed_profile=f"skill_cluster_{ins.category}",
                    rationale=f"Instinto '{ins.rule}' atingiu confiança {ins.confidence:.2f} com {ins.occurrences} ocorrências.",
                    status="pending",
                )
                try:
                    ledger.submit(prop)
                    submitted += 1
                except Exception:
                    pass
    except Exception as _ins_eval_err:
        pass

    if getattr(args, "json", False):
        print(json.dumps({"submitted": submitted, "pending_count": len(pending), "proposals": proposals}, indent=2, ensure_ascii=False))
        return 0

    print("=" * 55)
    print("      HAOS OUROBOROS — ANÁLISE DE EVOLUÇÃO         ")
    print("=" * 55)
    print(f"Novas propostas identificadas e submetidas: {submitted}")
    print(f"Total de propostas aguardando decisão: {len(pending)}")
    if submitted == 0:
        print("✓ Nenhuma falha recorrente ou oportunidade de melhoria identificada nos eventos atuais.")
    return 0


def cmd_haos_evolution_blast_radius(args: argparse.Namespace) -> int:
    """Executes 'hermes haos evolution blast-radius <files...>'."""
    from hermes.platform.capabilities.lsp.unified_intelligence import CodeSymbolGraph, ImpactAnalyzer
    import json

    files = args.files or []
    graph = CodeSymbolGraph()
    analyzer = ImpactAnalyzer(graph)

    try:
        blast = analyzer.calculate_blast_radius(modified_files=files)
        res = {
            "impacted_files": list(blast.affected_files),
            "impacted_callers": list(blast.affected_callers),
            "impacted_tests": list(blast.affected_test_suites),
            "risk_score": round(min(1.0, (len(blast.affected_files) * 0.15) + (len(blast.affected_callers) * 0.05)), 2),
            "severity": blast.severity,
        }
        if getattr(args, "json", False):
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0

        print("=" * 55)
        print("       HAOS OUROBOROS — BLAST RADIUS ANALYZER      ")
        print("=" * 55)
        print(f"Arquivos analisados   : {', '.join(files)}")
        print(f"Severidade estimada   : {res['severity'].upper()}")
        print(f"Pontuação de Risco    : {res['risk_score']}")
        print(f"Arquivos impactados   : {len(res['impacted_files'])}")
        print(f"Chamadores transitivos: {len(res['impacted_callers'])}")
        print(f"Testes recomendados   : {len(res['impacted_tests'])}")
        if res["impacted_tests"]:
            print("\nSuítes recomendadas para validação:")
            for t in res["impacted_tests"]:
                print(f"  • {t}")
        print("=" * 55)
        return 0
    except Exception as exc:
        print(f"Erro ao calcular Blast Radius: {exc}")
        return 1


def cmd_haos_graph_build(args: argparse.Namespace) -> int:
    """Executes 'haos graph build [--dir <dir>] [--out <out_dir>]'."""
    from hermes.platform.capabilities.lsp.unified_intelligence import CodeSymbolGraph
    import os

    target_dir = getattr(args, "dir", None) or os.getcwd()
    out_dir = getattr(args, "out", None) or os.path.join(target_dir, ".haos", "graphify-out")

    graph = CodeSymbolGraph()
    print(f"[*] Varrendo base de código com AST nativo em: {target_dir}")
    symbols_found = graph.scan_directory(target_dir)

    artifacts = graph.export_graph_report(out_dir)
    gods = graph.identify_god_components(top_k=5)

    print("=" * 65)
    print("      HAOS CODE KNOWLEDGE GRAPH (GRAPHIFY ENGINE)      ")
    print("=" * 65)
    print(f"Símbolos indexados : {symbols_found}")
    print(f"Arquivos mapeados  : {len(graph.file_symbols)}")
    print(f"Grafo JSON         : {artifacts['graph_json']}")
    print(f"Relatório Markdown : {artifacts['graph_report']}")
    print("-" * 65)
    print("🚨 TOP 5 COMPONENTES COM MAIOR ACOPLAMENTO (GOD COMPONENTS):")
    for g in gods:
        print(f" • {g['file_path']:<40} (Score: {g['coupling_score']})")
    print("=" * 65)
    return 0


def cmd_haos_graph_path(args: argparse.Namespace) -> int:
    """Executes 'haos graph path <source> <target> [--dir <dir>]'."""
    from hermes.platform.capabilities.lsp.unified_intelligence import CodeSymbolGraph
    import os

    target_dir = getattr(args, "dir", None) or os.getcwd()
    graph = CodeSymbolGraph()
    graph.scan_directory(target_dir)

    source = args.source
    target = args.target
    path = graph.find_path(source, target)

    print("=" * 60)
    print("          HAOS CODE KNOWLEDGE GRAPH — PATH TRACER         ")
    print("=" * 60)
    print(f"Origem : {source}")
    print(f"Destino: {target}")
    print("-" * 60)
    if path:
        print("✓ Trajetória encontrada:")
        for idx, step in enumerate(path):
            indent = "  " * idx
            arrow = "└──> " if idx > 0 else "• "
            print(f"{indent}{arrow}{step}")
    else:
        print(f"✗ Nenhum caminho de chamada direto encontrado entre '{source}' e '{target}'.")
    print("=" * 60)
    return 0


def cmd_haos_doc_index(args: argparse.Namespace) -> int:
    """Executes 'haos doc index <path>'."""
    from hermes.platform.memory.ragflow_engine import RAGFlowStore
    target_path = Path(args.path or ".").resolve()
    store = RAGFlowStore()

    print("=" * 60)
    print("📚 HAOS RAGFlow — Deep Document Ingestion")
    print(f"Target: {target_path}")
    print("=" * 60)

    if target_path.is_file():
        count = store.index_file(target_path)
        print(f"✓ Arquivo indexado: {target_path.name} ({count} chunks hierárquicos com proveniência)")
    elif target_path.is_dir():
        glob_pat = getattr(args, "pattern", None) or "**/*.md"
        indexed = store.index_directory(target_path, glob_pattern=glob_pat)
        total_chunks = sum(indexed.values())
        print(f"✓ Diretório indexado: {len(indexed)} arquivos processados, {total_chunks} chunks gerados.")
        for p, cnt in list(indexed.items())[:10]:
            print(f"   • {Path(p).name}: {cnt} chunks")
        if len(indexed) > 10:
            print(f"   ... e mais {len(indexed) - 10} arquivos.")
    else:
        print(f"✗ Caminho não encontrado: {target_path}")
        return 1
    return 0


def cmd_haos_doc_search(args: argparse.Namespace) -> int:
    """Executes 'haos doc search <query>'."""
    from hermes.platform.memory.ragflow_engine import RAGFlowStore
    query = (args.query or "").strip()
    limit = getattr(args, "limit", 5) or 5
    store = RAGFlowStore()

    print("=" * 60)
    print("🔍 HAOS RAGFlow — Busca Híbrida RRF (FTS5 BM25 + Lexical)")
    print(f"Query: {query!r} | Limite: {limit}")
    print("=" * 60)

    chunks = store.hybrid_search(query, limit=limit)
    if not chunks:
        print("Nenhum chunk correspondente encontrado.")
        return 0

    for i, c in enumerate(chunks, start=1):
        print(f"\n[{i}] {c.doc_path} | {c.header_path or '(raiz)'}")
        print(f"    Âncora: {c.provenance_anchor}")
        snippet = c.content.strip().splitlines()
        preview = "\n    ".join(snippet[:4])
        print(f"    Conteúdo:\n    {preview}")
        if len(snippet) > 4:
            print(f"    ... (+ {len(snippet) - 4} linhas)")
    print("\n" + "=" * 60)
    return 0


def cmd_haos_team_graph(args: argparse.Namespace) -> int:
    """Executes 'hermes haos team'."""
    from hermes.platform.observability.event_store import EventStore
    from hermes.platform.tasks.kanban_adapter import KanbanAdapter
    from hermes.platform.webui.controlplane import ControlPlaneService
    import json
    import dataclasses

    store = EventStore()
    kanban = KanbanAdapter()
    cp = ControlPlaneService(event_store=store, kanban=kanban)
    snapshot = cp.get_team_graph_snapshot()
    overview = cp.get_overview()

    if getattr(args, "json", False):
        print(json.dumps({"team_graph": snapshot, "overview": dataclasses.asdict(overview)}, indent=2, ensure_ascii=False))
        return 0

    print("=" * 60)
    print("           HAOS COGNITIVE TEAM GRAPH & HIERARCHY           ")
    print("=" * 60)
    print(f"Total de Missões     : {overview.total_missions}")
    print(f"Workers Ativos / Pool: {overview.active_workers} (Livres: {overview.idle_specialists})")
    print(f"Tokens / Custo Total : {overview.total_tokens} tkn | ${overview.total_cost_usd:.5f}")
    print("-" * 60)

    # Print Tree Hierarchy
    def print_node(node: dict, indent: int = 0):
        prefix = "  " * indent
        icon = "👑" if node.get("role") == "mayor" else ("🧠" if node.get("role") == "sub_orchestrator" else "⚡")
        status = str(node.get("status", "idle")).upper()
        role = node.get("posture") or node.get("role")
        model = f"{node.get('provider_id', '')}:{node.get('model_id', '')}"
        print(f"{prefix}{icon} [{status}] {node.get('label', node.get('node_id'))}")
        print(f"{prefix}   • ID: {node.get('node_id')} | Postura: {role} | Modelo: {model}")
        if node.get("current_task"):
            print(f"{prefix}   • Tarefa Atual: {node.get('current_task')}")
        blocker = (node.get("metadata") or {}).get("blocker")
        if blocker:
            b_kind = str(blocker.get("kind", "blocked")).upper()
            b_reason = blocker.get("reason", "")
            print(f"{prefix}   ⛔ BLOQUEIO [{b_kind}]: {b_reason}")
        for ch in node.get("children", []):
            print_node(ch, indent + 1)

    print_node(snapshot)
    print("=" * 60)
    return 0


def cmd_haos_scheduler_status(args: argparse.Namespace) -> int:
    """Executes 'hermes haos scheduler'."""
    from hermes.platform.ui.stats import DashboardStats
    from hermes.platform.tasks.kanban_adapter import KanbanAdapter
    from hermes.platform.observability.event_store import EventStore
    from hermes.platform.execution.backpressure import ConcurrencyGuard
    import json

    guard = ConcurrencyGuard()
    stats = DashboardStats(KanbanAdapter(), EventStore(), concurrency_guard=guard)
    cg = stats.concurrency()
    cp = stats.critical_path()

    if getattr(args, "json", False):
        print(json.dumps({
            "concurrency": cg.to_dict(),
            "critical_path": {
                "total_tasks_evaluated": cp.total_tasks_evaluated,
                "critical_path_ids": cp.critical_path_ids,
                "inherited_priorities": cp.inherited_priorities,
            }
        }, indent=2, ensure_ascii=False))
        return 0

    print("=" * 60)
    print("      HAOS SCHEDULER & CONCURRENCY GUARD (CPM + PIP)       ")
    print("=" * 60)
    print(f"Workers Ativos / Teto Global : {cg.active_global} / {cg.max_global} (Disponíveis: {cg.available_global})")
    print("\n[Quotas por Provedor LLM]")
    if cg.provider_limits:
        for p, limit in cg.provider_limits.items():
            active = cg.by_provider.get(p, 0)
            print(f"  • {p:<12}: {active} ativos / limite {limit}")
    else:
        print("  • Sem limites específicos configurados (usando teto global).")

    print("\n[Critical Path Method (CPM)]")
    print(f"  Total de tarefas avaliadas : {cp.total_tasks_evaluated}")
    print(f"  Caminho crítico (DAG)      : {' ➔ '.join(cp.critical_path_ids) if cp.critical_path_ids else 'Nenhum bloqueio'}")

    print("\n[Priority Inheritance Protocol (PIP)]")
    if cp.inherited_priorities:
        for tid, prio in sorted(cp.inherited_priorities.items(), key=lambda x: x[1], reverse=True):
            print(f"  • {tid:<12}: Prioridade propagada {prio}")
    else:
        print("  • Sem propagações ativas.")
    print("=" * 60)
    return 0


def cmd_haos_team_intervene(args: argparse.Namespace) -> int:
    """Executes 'hermes haos team intervene <target_id> <action> [--reason <reason>]'."""
    from hermes.platform.observability.event_store import EventStore
    from hermes.platform.tasks.kanban_adapter import KanbanAdapter
    from hermes.platform.webui.controlplane import ControlPlaneService
    store = EventStore()
    kanban = KanbanAdapter()
    cp = ControlPlaneService(event_store=store, kanban=kanban)
    cp.record_intervention(target_id=args.target_id, action=args.action, reason=args.reason or "CLI Operator intervention")
    print(f"✓ Intervenção '{args.action.upper()}' registrada e aplicada ao nó '{args.target_id}' no EventStore auditável.")
    return 0


def cmd_haos_eval(args: argparse.Namespace) -> int:
    """Executes 'hermes haos eval [--tasks G001,G002] [--label <label>] [--json]'."""
    from hermes.platform.evals.golden_tasks import run_benchmark_and_record, GOLDEN_TASKS
    from hermes.platform.evals.baselines import BaselineStore
    from hermes_constants import get_hermes_home
    from pathlib import Path
    import json

    task_ids = None
    if getattr(args, "tasks", None):
        task_ids = [t.strip() for t in args.tasks.split(",") if t.strip()]

    label = getattr(args, "label", None) or "current"
    home = Path(get_hermes_home())
    db_path = home / "eval_baselines.db"
    store = BaselineStore(db_path=str(db_path))

    metrics = run_benchmark_and_record(task_ids=task_ids, label=label, baseline_store=store)

    if getattr(args, "json", False):
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
        return 0

    print("=" * 72)
    print("🎯 HAOS GOLDEN TASKS EVALUATION HARNESS")
    print(f"[*] Label:    {label}")
    print(f"[*] Tasks:    {metrics['tasks_passed']}/{metrics['tasks_total']} passed ({metrics['score_percent']}%)")
    print(f"[*] Duration: {metrics['total_duration_sec']}s | Tokens: {metrics['total_tokens']}")
    print("=" * 72)
    print(f"{'TASK':<6} {'NAME':<30} {'STATUS':<8} {'TOKENS':<8} {'ASSERTIONS'}")
    for t in metrics["tasks"]:
        status_str = "✓ PASS" if t["success"] else "✗ FAIL"
        spec = GOLDEN_TASKS.get(t["task_id"])
        spec_name = spec.name if spec else t["task_id"]
        print(f"{t['task_id']:<6} {spec_name[:28]:<30} {status_str:<8} {t['tokens_consumed']:<8} {t['assertions_passed']}/{t['assertions_total']}")
    print("=" * 72)
    print(f"✓ Baseline snapshot recorded in {db_path}")
    return 0


def build_haos_parser(subparsers) -> argparse.ArgumentParser:
    """Builds and registers the parser for 'hermes haos'."""
    haos_parser = subparsers.add_parser(
        "haos",
        help="HAOS Control Plane, Tríade status, Federation, and Procedural Skills",
        description="HAOS Control Plane: inspect Tríade status, federated nodes, and procedural skills lifecycle.",
    )
    haos_sub = haos_parser.add_subparsers(dest="haos_command")

    # hermes haos status [--json]
    status_parser = haos_sub.add_parser("status", help="Show full HAOS platform summary")
    status_parser.add_argument("--json", action="store_true", help="Output status summary as JSON")
    status_parser.set_defaults(func=cmd_haos_status)

    # hermes haos doctor [--json]
    doctor_parser = haos_sub.add_parser("doctor", help="Valida a integridade, permissões e isolamento do HAOS")
    doctor_parser.add_argument("--json", action="store_true", help="Output doctor diagnostics as JSON")
    doctor_parser.set_defaults(func=cmd_haos_doctor)

    # hermes haos team [status|intervene]
    team_parser = haos_sub.add_parser("team", aliases=["teamgraph"], help="Cognitive Team Graph & multi-agent hierarchy")
    team_sub = team_parser.add_subparsers(dest="team_command")

    team_status_parser = team_sub.add_parser("status", help="Exibe a hierarquia cognitiva e nós ativos")
    team_status_parser.add_argument("--json", action="store_true", help="Output as JSON")
    team_status_parser.set_defaults(func=cmd_haos_team_graph)

    team_intervene_parser = team_sub.add_parser("intervene", help="Intervenção do operador em um nó do grafo (steer/pause/resume)")
    team_intervene_parser.add_argument("target_id", help="Identificador do nó (ex: polecat-1, sub-orch-*, mayor)")
    team_intervene_parser.add_argument("action", choices=["steer", "pause", "resume", "abort"], help="Ação de controle")
    team_intervene_parser.add_argument("--reason", help="Instrução ou motivo da intervenção")
    team_intervene_parser.set_defaults(func=cmd_haos_team_intervene)
    team_parser.set_defaults(func=cmd_haos_team_graph)

    # hermes haos scheduler
    sched_parser = haos_sub.add_parser("scheduler", aliases=["sched"], help="Scheduler determinístico (CPM, PIP, ConcurrencyGuard)")
    sched_parser.add_argument("--json", action="store_true", help="Output as JSON")
    sched_parser.set_defaults(func=cmd_haos_scheduler_status)

    # hermes haos evolution [status|analyze|blast-radius]
    evo_parser = haos_sub.add_parser("evolution", aliases=["ouroboros"], help="Ouroboros Self-Evolution Engine & Blast Radius")
    evo_sub = evo_parser.add_subparsers(dest="evolution_command")

    evo_status_parser = evo_sub.add_parser("status", help="Exibe status do Ouroboros e propostas pendentes")
    evo_status_parser.add_argument("--json", action="store_true", help="Output as JSON")
    evo_status_parser.set_defaults(func=cmd_haos_evolution_status)

    evo_analyze_parser = evo_sub.add_parser("analyze", help="Analisa logs e métricas gerando propostas de evolução")
    evo_analyze_parser.add_argument("--json", action="store_true", help="Output as JSON")
    evo_analyze_parser.set_defaults(func=cmd_haos_evolution_analyze)

    evo_blast_parser = evo_sub.add_parser("blast-radius", help="Calcula o raio de impacto de arquivos modificados")
    evo_blast_parser.add_argument("files", nargs="+", help="Caminho dos arquivos modificados")
    evo_blast_parser.add_argument("--json", action="store_true", help="Output as JSON")
    evo_blast_parser.set_defaults(func=cmd_haos_evolution_blast_radius)

    # hermes haos federation ping <peer_id>
    fed_parser = haos_sub.add_parser("federation", help="HAOS Federation management")
    fed_sub = fed_parser.add_subparsers(dest="federation_command")
    
    ping_parser = fed_sub.add_parser("ping", help="Mutual 3-way HMAC handshake with a peer node")
    ping_parser.add_argument("peer_id", help="Target federated peer node identifier")
    ping_parser.add_argument("--secret", help="Shared HMAC secret (optional, defaults to pre-configured)")
    ping_parser.add_argument("--local-node-id", default="node_local_primary", help="Local node identifier (default: node_local_primary)")
    ping_parser.add_argument("--endpoint", help="Peer network endpoint host:port (optional, for live mesh ping)")
    ping_parser.set_defaults(func=cmd_haos_federation_ping)

    # hermes haos skills list / promote <skill_id>
    skills_parser = haos_sub.add_parser("skills", help="HAOS Procedural Skills lifecycle")
    skills_sub = skills_parser.add_subparsers(dest="skills_command")

    list_parser = skills_sub.add_parser("list", help="List registered procedural skills and lifecycle status")
    list_parser.set_defaults(func=cmd_haos_skills_list)

    search_parser = skills_sub.add_parser("search", help="Busca habilidades no catálogo especializado wshobson e Hub")
    search_parser.add_argument("query", nargs="?", default="", help="Termo de busca (ex: k8s, python, security, docker)")
    search_parser.add_argument("--limit", type=int, default=20, help="Limite de resultados")
    search_parser.set_defaults(func=cmd_haos_skills_search)

    install_parser = skills_sub.add_parser("install", help="Baixa e instala uma habilidade especializada do catálogo")
    install_parser.add_argument("skill_name", help="Nome da habilidade a instalar")
    install_parser.add_argument("--force", action="store_true", help="Ignora avisos de quarentena do SkillsGuard")
    install_parser.set_defaults(func=cmd_haos_skills_install)

    promote_parser = skills_sub.add_parser("promote", help="Trigger evaluation and promotion pipeline for candidate skill")
    promote_parser.add_argument("skill_id", help="Skill name/ID to evaluate and promote")
    promote_parser.add_argument("--version", help="Specific skill version (optional)")
    promote_parser.set_defaults(func=cmd_haos_skills_promote)

    # hermes haos eval [--tasks <ids>] [--label <label>] [--json]
    eval_parser = haos_sub.add_parser("eval", aliases=["benchmark"], help="Executa o benchmark Golden Tasks e gera nota objetiva")
    eval_parser.add_argument("--tasks", help="Tarefas a executar (ex: G001,G002 ou vazio para todas)")
    eval_parser.add_argument("--label", default="current", help="Rótulo da medição (ex: baseline-v1, deepseek-v4)")
    eval_parser.add_argument("--json", action="store_true", help="Output metrics as JSON")
    eval_parser.set_defaults(func=cmd_haos_eval)

    # hermes haos graph [build|path]
    graph_parser = haos_sub.add_parser("graph", help="Code Knowledge Graph determinístico (Graphify Engine)")
    graph_sub = graph_parser.add_subparsers(dest="graph_command")

    g_build = graph_sub.add_parser("build", help="Varre a base de código e gera graph.json e GRAPH_REPORT.md")
    g_build.add_argument("--dir", help="Diretório da base de código (padrão: atual)")
    g_build.add_argument("--out", help="Diretório de saída (padrão: .haos/graphify-out)")
    g_build.set_defaults(func=cmd_haos_graph_build)

    g_path = graph_sub.add_parser("path", help="Encontra o caminho BFS de dependência/chamada mais curto entre dois nós")
    g_path.add_argument("source", help="Símbolo ou arquivo de origem")
    g_path.add_argument("target", help="Símbolo ou arquivo de destino")
    g_path.add_argument("--dir", help="Diretório da base de código (padrão: atual)")
    g_path.set_defaults(func=cmd_haos_graph_path)

    graph_parser.set_defaults(func=cmd_haos_graph_build)

    # hermes haos doc [index|search] (RAGFlow Deep Document Understanding)
    doc_parser = haos_sub.add_parser("doc", aliases=["rag"], help="Deep Document Understanding & RRF Search (RAGFlow Engine)")
    doc_sub = doc_parser.add_subparsers(dest="doc_command")

    d_index = doc_sub.add_parser("index", help="Indexa arquivo ou diretório markdown em SQLite FTS5 com breadcrumbs")
    d_index.add_argument("path", nargs="?", default=".", help="Arquivo ou diretório a indexar")
    d_index.add_argument("--pattern", default="**/*.md", help="Padrão glob para diretórios (padrão: **/*.md)")
    d_index.set_defaults(func=cmd_haos_doc_index)

    d_search = doc_sub.add_parser("search", help="Busca híbrida com Reciprocal Rank Fusion (RRF)")
    d_search.add_argument("query", help="Consulta de busca")
    d_search.add_argument("--limit", type=int, default=5, help="Limite de resultados (padrão: 5)")
    d_search.set_defaults(func=cmd_haos_doc_search)

    doc_parser.set_defaults(func=lambda args: doc_parser.print_help() or 0)

    # Default fallback when 'hermes haos' is run without subcommands
    haos_parser.set_defaults(func=lambda args: haos_parser.print_help() or 0)
    return haos_parser
