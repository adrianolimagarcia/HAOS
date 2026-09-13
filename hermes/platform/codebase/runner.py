"""End-to-end pipeline for the HAOS Codebase Wiki.

``run_index(root, out_dir, ...)`` wires the four passes together:

1. ``indexer.scan_facts``   — parse every ``.py`` under ``root`` (cache-served);
2. ``graph.build_graph``    — resolve facts into nodes/edges;
3. ``wiki.render_all``      — write ``graph.json`` + ``index.md`` + articles.

The cache lives under ``<out_dir>/cache/`` so a re-run after touching one file
re-parses only that file, and the rendered artifacts stay byte-stable when the
tree did not change. Everything is stdlib-only and offline.

``tree_signature`` / ``watch`` add the Fase-3 ``--watch`` mode: a cheap mtime
fingerprint decides whether a full re-index is needed, and the re-index itself
is incremental through the sha cache.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from . import clusters, graph, indexer, wiki


@dataclass
class IndexReport:
    root: Path
    out_dir: Path
    file_count: int = 0
    freshly_parsed: List[str] = field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0
    community_count: int = 0
    truncated: bool = False
    written: Dict[str, Path] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "root": str(self.root),
            "out_dir": str(self.out_dir),
            "file_count": self.file_count,
            "freshly_parsed": self.freshly_parsed,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "community_count": self.community_count,
            "truncated": self.truncated,
            "written": {k: str(v) for k, v in self.written.items()},
        }


def run_index(
    root: Path,
    out_dir: Path,
    *,
    skip: Optional[Set[str]] = None,
    max_files: Optional[int] = None,
    markdown: bool = False,
) -> IndexReport:
    """Index ``root`` into ``out_dir`` (cache + wiki artifacts).

    ``skip`` overrides the default exclusion set; ``max_files`` caps the number
    of modules processed (deterministic prefix, visible truncation marker);
    ``markdown`` (Fase 3) adds ``.md`` files as concept nodes with ``cita``
    edges to the code they name.
    """
    root = root.resolve()
    cache_dir = out_dir / "cache"
    facts, freshly_parsed, rel_paths = indexer.scan_facts(
        root, cache_dir, skip=skip, markdown=markdown
    )
    code_graph = graph.build_graph(
        facts,
        root=str(root),
        rel_paths=rel_paths,
        max_files=max_files,
    )
    written = wiki.render_all(
        code_graph, out_dir, limits_note=_limits_note(markdown=markdown)
    )
    node_to_comm, comms = clusters.assign_communities(code_graph)
    report = IndexReport(
        root=root,
        out_dir=out_dir,
        file_count=len(facts),
        freshly_parsed=freshly_parsed,
        node_count=len(code_graph.nodes),
        edge_count=len(code_graph.edges),
        community_count=len(comms),
        truncated=code_graph.truncated,
        written=written,
    )
    return report


def _limits_note(markdown: bool = False) -> str:
    if markdown:
        return ("- `.py` (AST) e `.md` (headings -> conceitos; menções -> arestas `cita`) "
                "são indexados; `.ts/.js` ficam de fora por decisão de escopo "
                "(extratores regex podem entrar depois).")
    return ("- Só código `.py` é indexado nesta versão (AST local); "
            "`.md`/`.js/.ts` entram nos extratores estendidos.")


# --------------------------------------------------------------------------
# --watch: cheap change detection + re-index loop
# --------------------------------------------------------------------------
TreeSignature = Dict[str, Tuple[int, int]]  # rel path -> (mtime_ns, size)


def tree_signature(root: Path, skip: Optional[Set[str]] = None) -> TreeSignature:
    """Fingerprint every file under ``root`` as ``rel -> (mtime_ns, size)``.

    Cheap enough to run every poll tick; excludes the skip dirs but not any
    output directory (the caller points ``out_dir`` outside ``root`` in watch
    mode, so re-indexed artifacts never trip the detector).
    """
    skip_set = set(indexer.SKIP_DIRS if skip is None else skip)
    sig: TreeSignature = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_set]
        for fn in filenames:
            full = Path(dirpath) / fn
            try:
                st = full.stat()
            except OSError:
                continue
            sig[str(full.relative_to(root))] = (st.st_mtime_ns, st.st_size)
    return sig


def _first_change(a: TreeSignature, b: TreeSignature) -> Optional[str]:
    """First rel path that changed between two signatures (added/removed/mutated)."""
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            return key
    return None


def watch(
    root: Path,
    out_dir: Path,
    *,
    skip: Optional[Set[str]] = None,
    max_files: Optional[int] = None,
    poll_seconds: float = 1.5,
    on_reindex: Optional[Callable[[IndexReport], None]] = None,
    stop: Optional[Callable[[], bool]] = None,
) -> None:
    """Re-index whenever the tree changes; loop until ``stop()`` returns True.

    Polls ``tree_signature`` every ``poll_seconds`` and re-runs ``run_index``
    on the first change. ``out_dir`` is excluded from the fingerprint only when
    it lives under ``root``; the default HERMES_HOME location is outside, so
    re-indexed artifacts never look like a source change.
    """
    # If out_dir is inside root, ignore it during change detection.
    out_resolved = out_dir.resolve()
    if _is_within(out_resolved, root.resolve()):
        ignore_rel = {str(out_resolved.relative_to(root.resolve()))}
    else:
        ignore_rel = set()
    sig = _sig_excluding(root, skip, ignore_rel)
    while not (stop() if stop else False):
        time.sleep(poll_seconds)
        new_sig = _sig_excluding(root, skip, ignore_rel)
        changed = _first_change(sig, new_sig)
        if changed is not None:
            report = run_index(root, out_dir, skip=skip, max_files=max_files)
            if on_reindex:
                on_reindex(report)
            sig = new_sig


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sig_excluding(
    root: Path, skip: Optional[Set[str]], ignore_rel: Set[str]
) -> TreeSignature:
    """tree_signature minus any path under an ignored directory (watch mode)."""
    return {
        rel: v for rel, v in tree_signature(root, skip).items()
        if not any(rel == p or rel.startswith(p + "/") for p in ignore_rel)
    }
