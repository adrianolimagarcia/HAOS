#!/usr/bin/env python3
"""
adr_prov.py - Ferramenta CLI determinística de consulta causal baseada no frontmatter PROV-O dos ADRs.

Contrato de autoridade: ADR-015 e okf/contratos/proveniencia-prov-o-no-haos.md.
Lê estritamente o frontmatter YAML dos ADRs no vault canônico e constrói o grafo causal.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml

DEFAULT_VAULT_ADRS_PATH = "/root/.haos/obsidian_vault/adrs"
ADR_ID_PATTERN = re.compile(r"^ADR-\d+$")


@dataclass
class ADRNode:
    id: str
    titulo: str
    status: str
    file_path: Path
    causado_by: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    affects: List[str] = field(default_factory=list)
    supersedes: List[str] = field(default_factory=list)
    superseded_by: Optional[str] = None
    was_generated_by: Optional[str] = None
    was_associated_with: Optional[str] = None
    was_derived_from: List[str] = field(default_factory=list)
    prov_causado_by: Optional[str] = None
    prov_affects: List[str] = field(default_factory=list)
    prov_supersedes: List[str] = field(default_factory=list)
    prov_superseded_by: Optional[str] = None


def normalize_adr_id(raw_id: str) -> str:
    cleaned = raw_id.strip()
    if cleaned.lower().startswith("adr:"):
        cleaned = cleaned[4:].strip()
    # Normalize adr-1 -> ADR-001 if someone writes adr-14 or ADR-14
    m = re.match(r"^adr-(\d+)$", cleaned, re.IGNORECASE)
    if m:
        num = int(m.group(1))
        return f"ADR-{num:03d}"
    return cleaned.upper()


def extract_frontmatter(file_path: Path) -> Optional[dict]:
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"Aviso: Não foi possível ler {file_path}: {exc}", file=sys.stderr)
        return None

    if not content.startswith("---"):
        return None

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None

    try:
        data = yaml.safe_load(parts[1])
        if isinstance(data, dict):
            return data
    except Exception as exc:
        print(f"Aviso: Erro ao parsear YAML em {file_path}: {exc}", file=sys.stderr)
    return None


def load_adr_vault(vault_path: Path) -> Tuple[Dict[str, ADRNode], List[str]]:
    """
    Carrega estritamente os ADRs a partir do frontmatter dos arquivos .md em vault_path.
    Retorna o dicionário {adr_id: ADRNode} e uma lista de alertas de higiene de grafo.
    """
    nodes: Dict[str, ADRNode] = {}
    hygiene_warnings: List[str] = []

    if not vault_path.is_dir():
        hygiene_warnings.append(f"Diretório do vault não encontrado: {vault_path}")
        return nodes, hygiene_warnings

    for f in sorted(vault_path.glob("*.md")):
        fm = extract_frontmatter(f)
        if not fm:
            try:
                content = f.read_text(encoding="utf-8")
            except Exception:
                hygiene_warnings.append(f"{f.name}: leitura_falhou")
                continue
            if not content.startswith("---") or len(content.split("---", 2)) < 3:
                hygiene_warnings.append(f"{f.name}: frontmatter_ausente")
            else:
                hygiene_warnings.append(f"{f.name}: yaml_malformado")
            continue

        raw_id = fm.get("id")
        if not raw_id or not isinstance(raw_id, str):
            hygiene_warnings.append(f"{f.name}: id_ausente")
            continue

        norm_id = normalize_adr_id(raw_id)
        if not ADR_ID_PATTERN.match(norm_id):
            hygiene_warnings.append(
                f"Arquivo {f.name} possui ID não-padrão '{raw_id}'. Ignorando para integridade estrita."
            )
            continue

        prov = fm.get("prov") if isinstance(fm.get("prov"), dict) else {}

        # Parse causado_by (pode vir como lista ou string)
        causado_by: List[str] = []
        raw_causado = fm.get("causado_by")
        if isinstance(raw_causado, list):
            causado_by = [str(x).strip() for x in raw_causado if x]
        elif isinstance(raw_causado, str) and raw_causado.strip():
            causado_by = [raw_causado.strip()]

        # Parse evidence
        evidence: List[str] = []
        raw_ev = fm.get("evidence")
        if isinstance(raw_ev, list):
            evidence = [str(x).strip() for x in raw_ev if x]
        elif isinstance(raw_ev, str) and raw_ev.strip():
            evidence = [raw_ev.strip()]

        # Parse affects
        affects: List[str] = []
        raw_aff = fm.get("affects")
        if isinstance(raw_aff, list):
            affects = [str(x).strip() for x in raw_aff if x]
        elif isinstance(raw_aff, str) and raw_aff.strip():
            affects = [raw_aff.strip()]

        # Parse supersedes
        supersedes: List[str] = []
        raw_sup = fm.get("supersedes")
        if isinstance(raw_sup, list):
            supersedes = [str(x).strip() for x in raw_sup if x]
        elif isinstance(raw_sup, str) and raw_sup.strip():
            supersedes = [raw_sup.strip()]

        superseded_by = fm.get("superseded_by")
        if superseded_by is not None:
            superseded_by = str(superseded_by).strip()

        prov_dict = prov if isinstance(prov, dict) else {}
        was_gen = prov_dict.get("wasGeneratedBy")
        was_assoc = prov_dict.get("wasAssociatedWith")

        raw_derived = prov_dict.get("wasDerivedFrom")
        was_derived: List[str] = []
        if isinstance(raw_derived, list):
            was_derived = [str(x).strip() for x in raw_derived if x]
        elif isinstance(raw_derived, str) and raw_derived.strip():
            was_derived = [raw_derived.strip()]

        prov_causado = prov_dict.get("causado_by")
        if prov_causado is not None:
            prov_causado = str(prov_causado).strip()

        raw_prov_affects = prov_dict.get("affects")
        raw_prov_supersedes = prov_dict.get("supersedes")
        raw_prov_superseded_by = prov_dict.get("superseded_by")

        node = ADRNode(
            id=norm_id,
            titulo=str(fm.get("titulo", "")).strip(),
            status=str(fm.get("status", "")).strip(),
            file_path=f,
            causado_by=causado_by,
            evidence=evidence,
            affects=affects,
            supersedes=supersedes,
            superseded_by=superseded_by,
            was_generated_by=str(was_gen).strip() if was_gen else None,
            was_associated_with=str(was_assoc).strip() if was_assoc else None,
            was_derived_from=was_derived,
            prov_causado_by=prov_causado,
            prov_affects=[str(x).strip() for x in raw_prov_affects if x]
            if isinstance(raw_prov_affects, list)
            else [],
            prov_supersedes=[str(x).strip() for x in raw_prov_supersedes if x]
            if isinstance(raw_prov_supersedes, list)
            else [],
            prov_superseded_by=str(raw_prov_superseded_by).strip()
            if raw_prov_superseded_by is not None
            else None,
        )
        nodes[norm_id] = node

    return nodes, hygiene_warnings


def check_ghost_entities(nodes: Dict[str, ADRNode]) -> List[str]:
    """
    Higiene de grafo: detecta entidades referenciadas como adr:ADR-XXX que não existem no vault.
    Descarta referências soltas em prosa como ADR-NNN que não estejam explicitamente no frontmatter.
    """
    ghosts: List[str] = []
    for node in nodes.values():
        targets: List[Tuple[str, str]] = []
        for item in node.causado_by:
            if item.lower().startswith("adr:"):
                target_id = normalize_adr_id(item)
                targets.append(("causado_by", target_id))
        for item in node.was_derived_from:
            if item.lower().startswith("adr:"):
                target_id = normalize_adr_id(item)
                targets.append(("wasDerivedFrom", target_id))
        for item in node.supersedes:
            if item.lower().startswith("adr:"):
                target_id = normalize_adr_id(item)
                targets.append(("supersedes", target_id))
            elif ADR_ID_PATTERN.match(item):
                targets.append(("supersedes", item))

        for rel, target_id in targets:
            if target_id not in nodes:
                ghosts.append(
                    f"[Entidade Fantasma] {node.id} aponta {rel} -> '{target_id}', mas '{target_id}' não existe no vault de ADRs."
                )

    return ghosts


def command_why(nodes: Dict[str, ADRNode], target_id: str) -> None:
    norm_id = normalize_adr_id(target_id)
    if norm_id not in nodes:
        print(f"Erro: ADR '{norm_id}' não encontrada no vault.", file=sys.stderr)
        sys.exit(1)

    print(f"\n================================================================================")
    print(f"  CADEIA CAUSAL DE PROVENIÊNCIA (WHY): {norm_id}")
    print(f"================================================================================\n")

    visited: Set[str] = set()

    def print_trace(curr_id: str, depth: int, via_rel: str) -> None:
        indent = "  " * depth
        prefix = f"{indent}└── [{via_rel}] " if depth > 0 else ""

        if curr_id not in nodes:
            print(f"{prefix}{curr_id} (ADR não resolvida / externa)")
            return

        node = nodes[curr_id]
        print(f"{prefix}{node.id}: \"{node.titulo}\" [{node.status}]")

        # Exibir metadados prov locais
        meta_indent = "  " * (depth + 1)
        if node.was_generated_by:
            print(f"{meta_indent}• prov:wasGeneratedBy: {node.was_generated_by}")
        if node.was_associated_with:
            print(f"{meta_indent}• prov:wasAssociatedWith: {node.was_associated_with}")

        # Evidências diretas
        if node.evidence:
            print(f"{meta_indent}• Evidências (wasDerivedFrom/evidence):")
            for ev in node.evidence:
                print(f"{meta_indent}   - {ev}")

        # Causas textuais / gatilhos raiz (itens em causado_by que não são adr:...)
        raw_causes = [c for c in node.causado_by if not c.lower().startswith("adr:")]
        if raw_causes:
            print(f"{meta_indent}• Gatilho Causal / Problema Raiz:")
            for rc in raw_causes:
                print(f"{meta_indent}   - {rc}")
        elif node.prov_causado_by and not node.prov_causado_by.lower().startswith("adr:"):
            print(f"{meta_indent}• Gatilho Causal (prov): {node.prov_causado_by}")

        if curr_id in visited:
            print(f"{meta_indent}(ciclo causal detectado para {curr_id}, interrompendo recursão)")
            return
        visited.add(curr_id)

        # Buscar causas pai: itens em causado_by que são adr:... ou itens em wasDerivedFrom
        parent_adrs: List[Tuple[str, str]] = []
        for item in node.causado_by:
            if item.lower().startswith("adr:"):
                pid = normalize_adr_id(item)
                if (pid, "causado_by") not in parent_adrs:
                    parent_adrs.append((pid, "causado_by"))

        for item in node.was_derived_from:
            if item.lower().startswith("adr:"):
                pid = normalize_adr_id(item)
                if (pid, "wasDerivedFrom") not in parent_adrs:
                    parent_adrs.append((pid, "wasDerivedFrom"))
            elif not item.lower().startswith("adr:"):
                # Origem de derivação não-ADR (ex: contract:, incident:, request:)
                print(f"{meta_indent}• prov:wasDerivedFrom (fonte externa): {item}")

        for parent_id, rel in parent_adrs:
            print_trace(parent_id, depth + 1, rel)

    print_trace(norm_id, 0, "root")
    print()


def command_desc(nodes: Dict[str, ADRNode], target_id: str) -> None:
    norm_id = normalize_adr_id(target_id)
    if norm_id not in nodes:
        print(f"Erro: ADR '{norm_id}' não encontrada no vault.", file=sys.stderr)
        sys.exit(1)

    node = nodes[norm_id]
    print(f"\n================================================================================")
    print(f"  EFEITOS E SUCESSÃO (DESC / EFFECTS): {norm_id}")
    print(f"================================================================================")
    print(f"ADR: {node.id} — \"{node.titulo}\" [{node.status}]\n")

    # Sucessão direta
    if node.superseded_by:
        print(f"⚠️  STATUS DE SUCESSÃO: SUPERSEDED por {node.superseded_by}")
    else:
        print(f"✅ STATUS DE SUCESSÃO: VIGENTE (superseded_by = null)")

    if node.supersedes:
        print(f"• Substitui diretamente (supersedes): {', '.join(node.supersedes)}")

    # Filhos no grafo (quem descende de norm_id)
    children_causado: List[str] = []
    children_derived: List[str] = []
    children_superseded: List[str] = []

    for other_id, other_node in sorted(nodes.items()):
        # causado_by
        for item in other_node.causado_by:
            if item.lower().startswith("adr:") and normalize_adr_id(item) == norm_id:
                children_causado.append(other_id)
        # wasDerivedFrom
        for item in other_node.was_derived_from:
            if item.lower().startswith("adr:") and normalize_adr_id(item) == norm_id:
                children_derived.append(other_id)
        # supersedes (other supersedes norm_id)
        for item in other_node.supersedes:
            if normalize_adr_id(item) == norm_id:
                children_superseded.append(other_id)

    print(f"\nDecisões e ADRs subsequentes causadas por {norm_id}:")
    if children_causado:
        for cid in sorted(set(children_causado)):
            cnode = nodes[cid]
            print(f"  └── [causou] {cnode.id}: \"{cnode.titulo}\" [{cnode.status}]")
    else:
        print("  (nenhuma ADR subsequente tem esta ADR como causa direta)")

    print(f"\nDecisões e ADRs derivadas de {norm_id} (prov:wasDerivedFrom):")
    if children_derived:
        for cid in sorted(set(children_derived)):
            cnode = nodes[cid]
            print(f"  └── [derivou] {cnode.id}: \"{cnode.titulo}\" [{cnode.status}]")
    else:
        print("  (nenhuma ADR subsequente declarou wasDerivedFrom explícito para esta ADR)")

    if children_superseded:
        print(f"\nSubstituída formalmente por:")
        for cid in sorted(set(children_superseded)):
            cnode = nodes[cid]
            print(f"  └── [superseded_by] {cnode.id}: \"{cnode.titulo}\" [{cnode.status}]")

    if node.affects:
        print(f"\nEscopos e Subsistemas afetados (affects):")
        for aff in node.affects:
            print(f"  - {aff}")
    print()


def command_graph(nodes: Dict[str, ADRNode]) -> None:
    print(f"\n================================================================================")
    print(f"  GRAFO DE ARESTAS CAUSAIS DE PROVENIÊNCIA (PROV-O)")
    print(f"================================================================================\n")

    edges: List[Tuple[str, str, str]] = []

    for adr_id, node in sorted(nodes.items()):
        # Causas (adr_id -> causado_by -> parent_id)
        for c in node.causado_by:
            if c.lower().startswith("adr:"):
                pid = normalize_adr_id(c)
                edges.append((adr_id, "causado_by", pid))
        # Derivações
        for d in node.was_derived_from:
            if d.lower().startswith("adr:"):
                pid = normalize_adr_id(d)
                edges.append((adr_id, "wasDerivedFrom", pid))
            else:
                edges.append((adr_id, "wasDerivedFrom", d))
        # Supersedes
        for s in node.supersedes:
            pid = normalize_adr_id(s)
            edges.append((adr_id, "supersedes", pid))
        # Superseded by
        if node.superseded_by:
            edges.append((adr_id, "superseded_by", normalize_adr_id(node.superseded_by)))
        # Atividade geradora
        if node.was_generated_by:
            edges.append((adr_id, "wasGeneratedBy", node.was_generated_by))

    print(f"{'Origem (Source)':<15} {'Relação (Edge)':<20} {'Destino (Target)'}")
    print(f"{'-'*15} {'-'*20} {'-'*40}")
    for src, edge, dst in edges:
        print(f"{src:<15} {edge:<20} {dst}")

    print(f"\nTotal de nós carregados: {len(nodes)}")
    print(f"Total de arestas de proveniência: {len(edges)}\n")


def command_check(nodes: Dict[str, ADRNode], warnings: List[str]) -> None:
    print(f"\n================================================================================")
    print(f"  HIGIENE E INTEGRIDADE DO GRAFO CAUSAL")
    print(f"================================================================================\n")

    ghosts = check_ghost_entities(nodes)

    if warnings:
        print("Alertas de leitura de arquivos:")
        for w in warnings:
            print(f"  ⚠️  {w}")
        print()

    if ghosts:
        print("Entidades Fantasmas / Inconsistências detectadas:")
        for g in ghosts:
            print(f"  ❌ {g}")
        print()
    else:
        print("✅ Nenhuma entidade fantasma encontrada. Todos os links causais apontam para ADRs reais.")

    print(f"Total de ADRs válidas no frontmatter: {len(nodes)}")
    for node_id, node in sorted(nodes.items()):
        prov_ok = "✅ PROV-O" if node.was_generated_by or node.was_derived_from else "⚠️ SEM BLOCO PROV"
        print(f"  - {node_id:<8} {prov_ok:<16} | \"{node.titulo[:55]}\"")
    print()


def _json_edge(source: str, relation: str, target: str) -> dict:
    return {"source": source, "relation": relation, "target": target}


def _json_trace(nodes: Dict[str, ADRNode], target_id: str) -> dict:
    root = normalize_adr_id(target_id)
    if root not in nodes:
        return {"root": root, "error": "not_found", "trace": [], "cycle_ids": [], "unresolved_ids": []}
    visited: Set[str] = set()
    trace: List[dict] = []

    def visit(current: str, depth: int, via: str) -> None:
        if current not in nodes:
            trace.append({"event": "unresolved", "id": current, "depth": depth, "via": via})
            return
        trace.append({"event": "node", "id": current, "depth": depth, "via": via})
        if current in visited:
            trace.append({"event": "cycle", "id": current, "depth": depth})
            return
        visited.add(current)
        parents: List[Tuple[str, str]] = []
        for item in nodes[current].causado_by:
            if item.lower().startswith("adr:"):
                pair = (normalize_adr_id(item), "causado_by")
                if pair not in parents:
                    parents.append(pair)
        for item in nodes[current].was_derived_from:
            if item.lower().startswith("adr:"):
                pair = (normalize_adr_id(item), "wasDerivedFrom")
                if pair not in parents:
                    parents.append(pair)
        for parent, relation in parents:
            visit(parent, depth + 1, relation)
        visited.remove(current)

    visit(root, 0, "root")
    return {
        "root": root,
        "trace": trace,
        "cycle_ids": sorted({item["id"] for item in trace if item["event"] == "cycle"}),
        "unresolved_ids": sorted({item["id"] for item in trace if item["event"] == "unresolved"}),
    }


def _json_ghosts(nodes: Dict[str, ADRNode]) -> List[dict]:
    result: List[dict] = []
    for node in nodes.values():
        for item in node.causado_by:
            if item.lower().startswith("adr:"):
                target = normalize_adr_id(item)
                if target not in nodes:
                    result.append(_json_edge(node.id, "causado_by", target))
        for item in node.was_derived_from:
            if item.lower().startswith("adr:"):
                target = normalize_adr_id(item)
                if target not in nodes:
                    result.append(_json_edge(node.id, "wasDerivedFrom", target))
        for item in node.supersedes:
            target = normalize_adr_id(item)
            if target not in nodes and (item.lower().startswith("adr:") or ADR_ID_PATTERN.match(item)):
                result.append(_json_edge(node.id, "supersedes", target))
    return result


def _json_graph(nodes: Dict[str, ADRNode]) -> dict:
    edges: List[dict] = []
    for node_id in sorted(nodes):
        node = nodes[node_id]
        for item in node.causado_by:
            if item.lower().startswith("adr:"):
                edges.append(_json_edge(node_id, "causado_by", normalize_adr_id(item)))
        for item in node.was_derived_from:
            edges.append(_json_edge(
                node_id,
                "wasDerivedFrom",
                normalize_adr_id(item) if item.lower().startswith("adr:") else item,
            ))
        for item in node.supersedes:
            edges.append(_json_edge(node_id, "supersedes", normalize_adr_id(item)))
        if node.superseded_by:
            edges.append(_json_edge(node_id, "superseded_by", normalize_adr_id(node.superseded_by)))
        if node.was_generated_by:
            edges.append(_json_edge(node_id, "wasGeneratedBy", node.was_generated_by))
    return {"nodes": sorted(nodes), "edges": edges}


def _json_check(nodes: Dict[str, ADRNode], warnings: List[str]) -> dict:
    graph = _json_graph(nodes)
    ghosts = _json_ghosts(nodes)
    cycles: Set[str] = set()
    for node_id in nodes:
        cycles.update(_json_trace(nodes, node_id)["cycle_ids"])
    return {
        "warnings": list(warnings),
        "ghosts": ghosts,
        "cycles": sorted(cycles),
        "nodes": graph["nodes"],
        "node_count": len(nodes),
    }


def command_json(nodes: Dict[str, ADRNode], warnings: List[str], command: str, target: Optional[str]) -> int:
    if command == "check":
        result = _json_check(nodes, warnings)
    elif command == "graph":
        result = _json_graph(nodes)
    elif command in ("why",):
        result = _json_trace(nodes, target or "")
    elif command in ("desc", "effects"):
        root = normalize_adr_id(target or "")
        if root not in nodes:
            result = {"root": root, "error": "not_found"}
        else:
            result = {
                "root": root,
                "superseded_by": nodes[root].superseded_by,
                "supersedes": list(nodes[root].supersedes),
                "children": {
                    relation: sorted({edge["source"] for edge in _json_graph(nodes)["edges"]
                                      if edge["target"] == root and edge["relation"] == relation})
                    for relation in ("causado_by", "wasDerivedFrom", "supersedes")
                },
                "affects": list(nodes[root].affects),
            }
    else:
        raise ValueError(f"comando JSON desconhecido: {command}")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if command == "check" and (not nodes or _json_check(nodes, warnings)["ghosts"] or _json_check(nodes, warnings)["cycles"] or warnings):
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consulta causal determinística baseada no frontmatter PROV-O de ADRs do HAOS."
    )
    parser.add_argument(
        "--vault",
        type=str,
        default=os.environ.get("HAOS_VAULT_ADRS", DEFAULT_VAULT_ADRS_PATH),
        help=f"Caminho do diretório de ADRs (default: {DEFAULT_VAULT_ADRS_PATH})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emite o resultado estruturado JSON para o gate de paridade.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # why ADR-XXX
    parser_why = subparsers.add_parser(
        "why", help="Sobe a árvore causal até causas originais e evidências."
    )
    parser_why.add_argument("adr_id", type=str, help="ID da ADR (ex: ADR-014 ou 014)")

    # desc ADR-XXX (alias: effects)
    parser_desc = subparsers.add_parser(
        "desc", aliases=["effects"], help="Desce a árvore exibindo efeitos, derivações e sucessões."
    )
    parser_desc.add_argument("adr_id", type=str, help="ID da ADR (ex: ADR-001 ou 001)")

    # graph
    subparsers.add_parser("graph", help="Exibe a matriz de arestas causais (source -> edge -> target).")

    # check / hygiene
    subparsers.add_parser("check", help="Executa auditoria de higiene e detecção de entidades fantasmas.")

    args = parser.parse_args()

    vault_path = Path(args.vault).resolve()
    nodes, warnings = load_adr_vault(vault_path)

    if args.json:
        target = getattr(args, "adr_id", None)
        raise SystemExit(command_json(nodes, warnings, args.command, target))

    if args.command == "why":
        command_why(nodes, args.adr_id)
    elif args.command in ("desc", "effects"):
        command_desc(nodes, args.adr_id)
    elif args.command == "graph":
        command_graph(nodes)
    elif args.command == "check":
        command_check(nodes, warnings)


if __name__ == "__main__":
    main()
