"""Governança de memória (backlog HAOS P5 — Etapa 3): proveniência, TTL, integridade.

O relatório de pesquisa trata memória como superfície de ataque (ZombieAgent,
OWASP ASI06 — memory poisoning) e exige que todo fato/lição/candidato seja
rastreável, tenha validade e tenha integridade verificável. As três políticas
vivem aqui, todas in-repo, sem ferramenta nova:

PROVENIÊNCIA
  Cada lição canônica (okf/lesson_*.md) carrega no frontmatter — escrita pelo
  dream (P11) no MESMO turno da promoção — source_session, session_title,
  destination, confidence, provenance, extracted_at (ISO-8601 UTC), model,
  evidence_span (o trecho literal do transcript que originou a lição) e
  skill_afetada (P2: o slug determinístico da skill afetada quando o destino é
  skill). O degrau de staging (P11, staging.py) já guarda a tríade
  sessão/timestamp/confiança por candidato (provenance, created_at/last_seen_at,
  confidence) — verificado pelo contrato em test_memory_governance.

TTL / VALIDADE
  Política de obsolescência (documentada em MEMORY_TTL_DAYS = 30): a literatura
  mediu corte de ruído "by roughly half" demovendo entrada não acessada em ~30
  dias. Aqui:
  - candidato pending no staging expira (status "expired") quando valid_to vence
    OU quando fica MEMORY_TTL_DAYS sem reforço (last_seen_at antigo) — ver
    MemoryStagingStore.expire. Demover NUNCA apaga: o registro permanece como
    trilha de auditoria.
  - lição canônica com valid_to vencido é reportada (list_obsolete_lessons) e
    demovida (demote_obsolete_lessons: re-save com obsolete: true + demoted_at).
  Não há rastreio de leitura por documento no repo; o proxy de acesso para
  lições canônicas é o valid_to explícito (e para candidatos, o last_seen_at do
  staging). Rastreio de acesso por leitura ficaria fora do escopo (inventaria
  infraestrutura sem consumidor concreto).

INTEGRIDADE
  Baseline SHA-256 dos arquivos canônicos de memória (okf/**/*.md +
  memory/staging/pending_candidates.json) em <home>/memory/integrity/baseline.json.
  MemoryIntegrityChecker.verify() detecta mudança FORA DE BANDA — arquivo
  novo/alterado/removido cujo hash difere do baseline SEM uma escrita registrada
  no mesmo turno — a defesa contra "memória envenenada que parece legítima".
  O fluxo de escrita legítimo chama register_write() no MESMO turno (o dream
  registra ao escrever), senão o gate vira falso positivo crônico (o próprio
  relatório aponta isso). Checagens estruturais complementares: o JSON do
  staging precisa parsear e cada registro precisa das chaves do contrato
  (validate_json_index); documentos canônicos precisam de frontmatter parseável
  e corpo não vazio, e lições no formato novo (com extracted_at) precisam da
  proveniência inteira (validate_canonical_documents).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Corte de ruído medido pela literatura (~30 dias sem acesso) — mesma base do
# demote do relatório ("não acessada em 30 dias").
MEMORY_TTL_DAYS = 30

# Chaves obrigatórias do contrato de cada candidato no staging (P11 + P5).
STAGING_REQUIRED_KEYS = ("key", "fact", "confidence", "provenance", "status")

# Proveniência completa exigida das lições no formato novo (com extracted_at).
LESSON_FULL_PROVENANCE_KEYS = (
    "source_session", "destination", "confidence",
    "extracted_at", "model", "evidence_span", "provenance",
)

_STAGING_REL = "memory/staging/pending_candidates.json"


def _rel_from_home(path: Path, home: Path) -> str:
    return str(path.relative_to(home)).replace(os.sep, "/")


def list_obsolete_lessons(okf_dir: Path, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Lições canônicas obsoletas: valid_to vencido ou marcada obsolete."""
    from hermes.platform.memory.okf import OKFStore  # function-level: lint A6

    now = time.time() if now is None else float(now)
    out: List[Dict[str, Any]] = []
    for doc in OKFStore(okf_dir).documents():
        if not Path(doc.relative_path).name.startswith("lesson_"):
            continue
        valid_to = doc.metadata.get("valid_to")
        expired = isinstance(valid_to, (int, float)) and float(valid_to) < now
        if bool(doc.metadata.get("obsolete")) or expired:
            out.append({"rel": doc.relative_path, "path": doc.filepath,
                        "obsolete": bool(doc.metadata.get("obsolete")), "valid_to": valid_to})
    return out


def demote_obsolete_lessons(okf_dir: Path, now: Optional[float] = None) -> int:
    """Demove lições canônicas com valid_to vencido: re-save do frontmatter com
    obsolete: true + demoted_at (NUNCA apaga — trilha de auditoria). Idempotente."""
    from hermes.platform.memory.okf import OKFStore  # function-level: lint A6

    now = time.time() if now is None else float(now)
    store = OKFStore(okf_dir)
    demoted = 0
    for doc in store.documents():
        if not Path(doc.relative_path).name.startswith("lesson_"):
            continue
        if doc.metadata.get("obsolete"):
            continue  # já demovida
        valid_to = doc.metadata.get("valid_to")
        if not (isinstance(valid_to, (int, float)) and float(valid_to) < now):
            continue
        meta = dict(doc.metadata)
        meta["obsolete"] = True
        meta["demoted_at"] = now
        store.save_document(
            title=str(doc.metadata.get("title") or doc.title),
            content=doc.body,
            doc_type=str(doc.metadata.get("type") or doc.doc_type),
            tags=doc.tags,
            filename=Path(doc.relative_path).name,
            extra_metadata=meta,
        )
        demoted += 1
    return demoted


class MemoryIntegrityChecker:
    """Baseline SHA-256 dos arquivos canônicos de memória + checagens estruturais."""

    def __init__(self, home: Optional[Path] = None):
        from hermes_constants import get_hermes_home  # function-level: lint A6

        self.home = Path(home) if home is not None else Path(get_hermes_home())

    @property
    def baseline_path(self) -> Path:
        return self.home / "memory" / "integrity" / "baseline.json"

    # ── Conjunto canônico ──────────────────────────────────────────────────────

    def canonical_files(self) -> List[Path]:
        """Arquivos canônicos de memória: lições okf/**/*.md + índice do staging."""
        out: List[Path] = []
        okf_dir = self.home / "okf"
        if okf_dir.is_dir():
            out.extend(sorted(p for p in okf_dir.rglob("*.md") if p.is_file()))
        staging = self.home / "memory" / "staging" / "pending_candidates.json"
        if staging.is_file():
            out.append(staging)
        return out

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    # ── Checagens estruturais ──────────────────────────────────────────────────

    def validate_json_index(self) -> List[Dict[str, Any]]:
        """O JSON do staging parseia e cada registro tem as chaves do contrato."""
        staging = self.home / "memory" / "staging" / "pending_candidates.json"
        if not staging.exists():
            return []
        try:
            data = json.loads(staging.read_text(encoding="utf-8"))
        except Exception as exc:
            return [{"kind": "corrupt_json", "rel": _STAGING_REL, "detail": str(exc)}]
        if not isinstance(data, dict):
            return [{"kind": "corrupt_json", "rel": _STAGING_REL,
                     "detail": "índice não é um mapeamento"}]
        violations: List[Dict[str, Any]] = []
        for key, rec in data.items():
            if not isinstance(rec, dict):
                violations.append({"kind": "invalid_record", "rel": _STAGING_REL,
                                   "detail": f"registro {key!r} não é dict"})
                continue
            missing = [k for k in STAGING_REQUIRED_KEYS if k not in rec]
            if missing:
                violations.append({"kind": "invalid_record", "rel": _STAGING_REL,
                                   "detail": f"{key}: faltando {missing}"})
        return violations

    @staticmethod
    def _parse_okf(content: str) -> "tuple[Dict[str, Any] | None, str]":
        from agent.skill_utils import parse_frontmatter  # function-level

        try:
            fm, body = parse_frontmatter(content)
        except Exception:
            return None, content
        return (fm if isinstance(fm, dict) else None), body

    def validate_canonical_documents(self) -> List[Dict[str, Any]]:
        """Conteúdo mínimo dos documentos canônicos: frontmatter parseável, corpo
        não vazio; lições no formato novo (com extracted_at) exigem a proveniência
        inteira do P5."""
        violations: List[Dict[str, Any]] = []
        okf_dir = self.home / "okf"
        if not okf_dir.is_dir():
            return violations
        for p in sorted(okf_dir.rglob("*.md")):
            if not p.is_file():
                continue
            rel = _rel_from_home(p, self.home)
            try:
                content = p.read_text(encoding="utf-8")
            except Exception as exc:
                violations.append({"kind": "unreadable", "rel": rel, "detail": str(exc)})
                continue
            if not content.strip():
                violations.append({"kind": "empty_body", "rel": rel, "detail": "documento canônico vazio"})
                continue
            frontmatter, body = self._parse_okf(content)
            if frontmatter is None:
                violations.append({"kind": "bad_frontmatter", "rel": rel,
                                   "detail": "frontmatter não parseia"})
                continue
            if not body.strip():
                violations.append({"kind": "empty_body", "rel": rel,
                                   "detail": "corpo vazio após o frontmatter"})
            if Path(rel).name.startswith("lesson_") and frontmatter.get("extracted_at"):
                missing = [k for k in LESSON_FULL_PROVENANCE_KEYS if k not in frontmatter]
                if missing:
                    violations.append({"kind": "missing_provenance", "rel": rel,
                                       "detail": "faltando: " + ", ".join(missing)})
        return violations

    # ── Baseline / verificação ────────────────────────────────────────────────

    def baseline(self) -> Dict[str, str]:
        return {_rel_from_home(p, self.home): self._sha256(p) for p in self.canonical_files()}

    def register_write(self) -> Dict[str, Any]:
        """Registra as escritas legítimas do turno: recalcula o baseline corrente.

        Chamado no MESMO turno do fluxo de escrita (dream/governança) — sem isso
        toda escrita legítima viraria falso positivo no verify seguinte (o
        próprio relatório aponta o risco: "o script de registro precisa ser parte
        do fluxo de escrita, senão o gate vira falso positivo crônico")."""
        manifest = {"version": 1, "written_at": time.time(), "files": self.baseline()}
        self.baseline_path.parent.mkdir(parents=True, exist_ok=True)
        self.baseline_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return manifest

    def verify(self) -> List[Dict[str, Any]]:
        """Detecta mudança FORA DE BANDA: hash dos canônicos difere do baseline
        sem escrita registrada no mesmo turno (ou arquivo novo/removido). Sem
        baseline e sem arquivos canônicos → limpo (nada a checar)."""
        violations: List[Dict[str, Any]] = []
        if not self.baseline_path.exists():
            if self.canonical_files():
                return [{"kind": "no_baseline", "rel": "memory/integrity/baseline.json",
                         "detail": "arquivos canônicos sem baseline registrado"}]
            return violations
        try:
            manifest = json.loads(self.baseline_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return [{"kind": "corrupt_baseline", "rel": "memory/integrity/baseline.json",
                     "detail": str(exc)}]
        baseline_files = manifest.get("files") if isinstance(manifest, dict) else {}
        if not isinstance(baseline_files, dict):
            baseline_files = {}
        current = self.baseline()
        for rel, digest in sorted(baseline_files.items()):
            cur = current.get(rel)
            if cur is None:
                violations.append({"kind": "deleted", "rel": rel,
                                   "detail": "arquivo canônico removido"})
            elif cur != digest:
                violations.append({"kind": "changed", "rel": rel,
                                   "detail": "hash difere do baseline (mudança fora de banda)"})
        for rel in sorted(current):
            if rel not in baseline_files:
                violations.append({"kind": "new", "rel": rel,
                                   "detail": "arquivo canônico novo sem escrita registrada"})
        return violations