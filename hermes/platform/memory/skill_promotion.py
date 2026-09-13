"""Promoção determinística lição → skill (backlog HAOS P2 — Etapa 3).

Fecha o elo que o relatório de pesquisa marcou como morto ("nada promove"): o
dream reflete (P11) e o auditor audita (P3), mas nenhum caminho transformava uma
lição canônica em (proposta de) skill. Este módulo É esse caminho — estende o
pipeline existente (dream/instincts), sem ferramenta nova (Footprint Ladder
rung 1) e sem remover nenhum cap.

Decisões documentadas (com a evidência que as sustenta)
-------------------------------------------------------
* CRITÉRIO DE PROMOÇÃO — uma lição canônica (gate do P11: confiança >= 0.85,
  ``MemoryCandidate.is_high_confidence``) é promovível quando TODAS valem:
    1. o MemoryRouter a classifica deterministicamente como ``skill`` — o
       vocabulário procedural do router (RE_SKILL: workflow/procedure/how to/
       step-by-step/recipe/playbook/...) é exatamente o que o destino "skill"
       significa no contrato do router (router.py: "Procedimentos operacionais
       padrão, fluxos reutilizáveis, receitas"). Fato classificado como
       core_user/obsidian/task/working não é skill — pertence ao próprio store.
    2. confiança >= 0.85 (o gate canônico do P11 que a lição já passou para
       existir em okf/);
    3. estrutura mínima: >= 30 chars e >= 5 palavras (uma instrução completa,
       não um fragmento) — o dream já descarta lições <= 15 chars; a promoção
       exige a barra mais alta porque uma skill carrega corpo.
* AÇÃO — ``create`` (nenhuma skill do parque cobre), ``refine`` (skill do
  domínio existe — g5 do relatório: "havendo skill do domínio, a ação é
  refine") ou ``ignore`` (default: evidência não-skill). Dedup por nome
  normalizado / slug / corpo verbatim / token de domínio no parque auditado —
  nunca se gera um SKILL.md com nome duplicado (regra unique_names do P3).
* GATE g7 (humano no volante) — o resultado é PROPOSTA persistida em
  <home>/memory/skill_proposals/proposals.json, nunca aplicação automática no
  parque. g1 (gain por caso de eval) não é implementável in-repo para lições
  arbitrárias (exigiria um eval por lição); sem g1, aplicar seria quebrar a
  fronteira da §8. A proposta guarda o SKILL.md COMPLETO (gerado e auditável),
  então a aplicação é uma cópia verificada, não uma re-derivação.
* GATE g3/g4 (P3) — todo SKILL.md gerado passa pelo auditor REAL
  (scripts/audit_skills.py, o mesmo do CI): frontmatter completo, name == dir,
  description <= 60 chars com ponto final, corpo na ordem HARDLINE do
  skills/AGENTS.md. Falha no auditor = fail-closed: a lição não gera proposta.
* LOADOUT (P3) — a promoção NUNCA escreve config de loadout/pins: a decisão de
  subir a skill a uma sessão é do teto (select_skill_loadout); a proposta
  registra ``loadout.decision = "deferred_to_loadout_cap"`` e o snapshot do
  teto/pins apenas informativo.
* REGISTRO — cada promoção preserva a proveniência: sessões-fonte, confiança,
  instante da extração, rel do documento okf e o conteúdo gerado. O instinto
  correspondente (mesmo namespace de hermes_cli/haos_cmd.py:391, project_scope
  "default") é marcado promovido, para o Ouroboros não propor a mesma lição
  duas vezes.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hermes.platform.context.memory.router import MemoryRouter
from hermes.platform.context.memory.staging import candidate_key

logger = logging.getLogger(__name__)

# Gate canônico do P11 (MemoryCandidate.is_high_confidence) — a lição SÓ existe
# em okf/ depois de passar por ele; a promoção re-exige o mesmo limiar.
PROMOTION_MIN_CONFIDENCE = 0.85
# Estrutura mínima da lição: instrução completa, não fragmento.
PROMOTION_MIN_LESSON_CHARS = 30
PROMOTION_MIN_WORDS = 5
# Categoria proposta para as skills geradas (mesma de skills/software-development).
PROMOTION_CATEGORY = "software-development"
PROPOSALS_FILENAME = "proposals.json"

# Boilerplate procedural (vocabulário do RE_SKILL do router) + stopwords pt/en:
# o nome da skill nasce dos TOKENS DE CONTEÚDO da lição, não do "workflow" que
# a classificou — senão toda skill se chamaria "workflow-procedure".
_STOP = frozenset({
    "workflow", "procedure", "procedimento", "fluxo", "recipe", "receita",
    "playbook", "step", "steps", "passo", "passos", "how", "to", "como",
    "reusable", "reutilizavel", "guidelines", "guideline", "diretriz",
    "sempre", "nunca", "antes", "depois", "rodar", "roda", "use", "usar",
    "the", "a", "an", "of", "for", "on", "by", "with", "and", "or", "in",
    "at", "from", "de", "da", "do", "das", "dos", "para", "com", "sem", "em",
    "e", "o", "os", "as", "um", "uma", "que", "se", "no", "na", "nos",
    "nas", "por", "pelo", "pela", "ao", "à", "é",
})


@dataclass(frozen=True)
class PromotionAssessment:
    """Veredito puro do critério de promoção (sem I/O, sem parque)."""
    action: str  # "create" | "ignore"
    reason: str


def _meaningful_tokens(text: str) -> "frozenset[str]":
    return frozenset(
        w for w in re.findall(r"[a-z0-9]+", text.lower())
        if w not in _STOP and len(w) > 2
    )


def skill_name_from_lesson(fact: str) -> str:
    """Slug determinístico e estável do assunto da lição (nome candidato da skill).

    Remove o boilerplate procedural/stopwords e junta até 4 tokens de conteúdo
    com hífens. A mesma lição gera sempre o mesmo nome; assuntos distintos
    geram nomes distintos.
    """
    words = re.findall(r"[a-z0-9]+", fact.lower())
    content = [w for w in words if w not in _STOP and len(w) > 1]
    base = content[:4] or words[:3]
    name = "-".join(base)[:48].strip("-")
    return name or "skill-proposta"


def description_from_lesson(fact: str) -> str:
    """Descrição HARDLINE (skills/AGENTS.md regra 1): <= 60 chars, uma frase,
    ponto final, sem palavras de marketing — derivada da própria lição."""
    desc = " ".join(fact.strip().split())
    if len(desc) > 60:
        head = desc[:60]
        desc = head.rsplit(" ", 1)[0] if " " in head else head
    desc = desc.rstrip()
    if not desc.endswith("."):
        desc = desc + "."
    return desc[:60]


def assess_lesson_promotion(fact: str, destination: str, confidence: float) -> PromotionAssessment:
    """Critério determinístico lição → skill (ver docstring do módulo)."""
    if destination != "skill":
        return PromotionAssessment(
            "ignore",
            f"destino {destination!r} != skill (classificacao deterministica do MemoryRouter)",
        )
    if confidence < PROMOTION_MIN_CONFIDENCE:
        return PromotionAssessment(
            "ignore",
            f"confianca {confidence} < {PROMOTION_MIN_CONFIDENCE} (gate canonico do P11)",
        )
    fact = fact.strip()
    if len(fact) < PROMOTION_MIN_LESSON_CHARS:
        return PromotionAssessment(
            "ignore",
            f"licao curta ({len(fact)} chars < {PROMOTION_MIN_LESSON_CHARS})",
        )
    if len(fact.split()) < PROMOTION_MIN_WORDS:
        return PromotionAssessment("ignore", "licao sem estrutura de frase completa")
    return PromotionAssessment(
        "create",
        f"licao procedural canonica (confianca {confidence}) sem cobertura conhecida",
    )


def skill_md_content(
    name: str,
    lesson: str,
    *,
    description: str,
    lesson_key: str = "",
    source_session: str = "",
    confidence: float = PROMOTION_MIN_CONFIDENCE,
    extracted_at: str = "",
    provenance: Optional[List[str]] = None,
) -> str:
    """Gera o SKILL.md completo da proposta: frontmatter completo (campos
    obrigatórios do auditor P3 + proveniência P5) e corpo na ordem HARDLINE do
    skills/AGENTS.md. O corpo é 100% derivado da lição — nunca texto inventado.
    """
    import yaml  # function-level: lint A6 de hermes/platform (stdlib no topo)

    frontmatter = {
        "name": name,
        "description": description,
        "version": "1.0.0",
        "author": "Hermes Agent (dream)",
        "license": "MIT",
        "platforms": ["linux", "macos"],
        "category": PROMOTION_CATEGORY,
        "metadata": {
            "hermes": {
                "category": PROMOTION_CATEGORY,
                "tags": ["dream-promoted", "lesson", "skill"],
                "related_skills": [],
            },
            "haos": {
                "promotion": {
                    "lesson_key": lesson_key,
                    "source_session": source_session,
                    "confidence": float(confidence),
                    "extracted_at": extracted_at,
                    "provenance": list(provenance or []),
                },
            },
        },
    }
    yaml_fm = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    body = (
        f"# {name} Skill\n\n"
        "Licao procedural canonica promovida pelo ciclo de aprendizado (P11 -> P2): "
        "regra reutilizavel extraida de sessoes reais e proposta como skill (g7: "
        "proposta, nao promocao automatica).\n\n"
        "## When to Use\n\n"
        f"Use quando a tarefa for: {lesson}\n\n"
        "## Prerequisites\n\n"
        "Nenhum alem do agente base.\n\n"
        "## How to Run\n\n"
        f"1. {lesson}\n\n"
        "## Quick Reference\n\n"
        f"- Regra: {lesson}\n\n"
        "## Procedure\n\n"
        f"1. {lesson}\n\n"
        "## Pitfalls\n\n"
        "Nenhum registrado na extracao da licao.\n\n"
        "## Verification\n\n"
        "Confira que a regra continua valida na proxima execucao real.\n"
    )
    return f"---\n{yaml_fm}\n---\n\n{body}"


class SkillProposalStore:
    """Registro persistente das propostas de promoção lição → skill.

    <home>/memory/skill_proposals/proposals.json, chaveado pela chave canônica
    da lição (candidate_key). Proveniência nunca é perdida: cada registro guarda
    as sessões-fonte, confiança, instante da extração e o SKILL.md gerado (ou o
    diff de refine). Construir o store não escreve nada (mkdir lazy em _save).
    """

    def __init__(self, root_dir: Optional[Path] = None):
        from hermes_constants import get_hermes_home  # function-level: lint A6

        self.root = Path(root_dir) if root_dir is not None else Path(get_hermes_home()) / "memory" / "skill_proposals"

    @property
    def index_file(self) -> Path:
        return self.root / PROPOSALS_FILENAME

    def load(self) -> Dict[str, Dict[str, Any]]:
        if not self.index_file.exists():
            return {}
        try:
            data = json.loads(self.index_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Skill proposals ilegíveis em %s: %s", self.index_file, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, records: Dict[str, Dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)  # lazy: instanciar não escreve
        self.index_file.write_text(
            json.dumps(records, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self.load().get(key)

    def add(self, key: str, proposal: Dict[str, Any]) -> Dict[str, Any]:
        records = self.load()
        records[key] = {**proposal, "key": key, "status": "proposed", "created_at": time.time()}
        self._save(records)
        return records[key]

    def list_proposals(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        records = list(self.load().values())
        if status is not None:
            records = [r for r in records if r.get("status") == status]
        return sorted(records, key=lambda r: r.get("created_at", 0.0))

    def mark_applied(self, key: str) -> bool:
        records = self.load()
        rec = records.get(key)
        if rec is None:
            return False
        rec["status"] = "applied"
        rec["applied_at"] = time.time()
        self._save(records)
        return True


class LessonSkillPromoter:
    """Orquestra o caminho lição canônica → proposta de skill (P2).

    Lê as lições canônicas de <home>/okf (lesson_*.md — o que o P11 promoveu),
    aplica o critério determinístico, dedupa no parque auditado, gera e audita o
    SKILL.md (create) ou o diff (refine), e registra a proposta com proveniência.
    """

    def __init__(
        self,
        home: Optional[Path] = None,
        okf_dir: Optional[Path] = None,
        skills_root: Optional[Path] = None,
        router: Any = None,
        instinct_store: Any = None,
        instinct_project_scope: str = "default",
    ):
        from hermes_constants import get_hermes_home, get_skills_dir  # function-level

        self.home = Path(home) if home is not None else Path(get_hermes_home())
        self.okf_dir = Path(okf_dir) if okf_dir is not None else self.home / "okf"
        self.skills_root = Path(skills_root) if skills_root is not None else Path(get_skills_dir())
        self.router = router if router is not None else MemoryRouter()
        self.store = SkillProposalStore(self.home / "memory" / "skill_proposals")
        self.instinct_store = instinct_store
        self.instinct_project_scope = instinct_project_scope

    # ── Leitura ────────────────────────────────────────────────────────────────

    def scan_lessons(self) -> List[Dict[str, Any]]:
        """Lições canônicas de okf/ (lesson_*.md, o formato do dream)."""
        from hermes.platform.memory.okf import OKFStore

        lessons: List[Dict[str, Any]] = []
        for doc in OKFStore(self.okf_dir).documents():
            if not Path(doc.relative_path).name.startswith("lesson_"):
                continue
            lessons.append({
                "fact": doc.body.strip(),
                "metadata": doc.metadata,
                "path": doc.filepath,
                "rel": doc.relative_path,
            })
        return sorted(lessons, key=lambda d: d["rel"])

    def audited_park(self) -> Dict[str, Dict[str, Any]]:
        """Skills do parque onde a skill seria criada, pelo MESMO iterator que o
        auditor do P3 usa (iter_skill_index_files) — dedup nunca diverge do load."""
        from agent.skill_utils import iter_skill_index_files, parse_frontmatter  # function-level

        park: Dict[str, Dict[str, Any]] = {}
        if not self.skills_root.is_dir():
            return park
        for skill_md in iter_skill_index_files(self.skills_root, "SKILL.md"):
            try:
                content = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm, _ = parse_frontmatter(content)
            name = str(fm.get("name") or skill_md.parent.name) if isinstance(fm, dict) else skill_md.parent.name
            park[name] = {
                "content": content,
                "description": str(fm.get("description") or "") if isinstance(fm, dict) else "",
            }
        return park

    # ── Dedup ──────────────────────────────────────────────────────────────────

    def covering_skill(
        self, fact: str, proposed_name: str, park: Dict[str, Dict[str, Any]]
    ) -> "Tuple[Optional[str], str]":
        """(skill, sinal) que cobre a lição, ou (None, "") — sinais: nome
        idêntico, slug normalizado, corpo verbatim, token de domínio (g5)."""
        if proposed_name in park:
            return proposed_name, "name"
        for name in park:
            if name.replace("-", "_") == proposed_name.replace("-", "_"):
                return name, "slug"
        for name, entry in park.items():
            if fact and fact in entry["content"]:
                return name, "verbatim"
        subject = _meaningful_tokens(fact)
        if subject:
            best, best_hits = None, 0
            for name in sorted(park):
                entry = park[name]
                tokens = _meaningful_tokens(name) | _meaningful_tokens(entry["description"])
                hits = len(tokens & subject)
                if hits > best_hits:
                    best, best_hits = name, hits
            if best is not None:
                return best, "domain"
        return None, ""

    # ── Geração / diff ─────────────────────────────────────────────────────────

    @staticmethod
    def _refine_diff(current: str, fact: str) -> str:
        """Diff determinístico: anexa a regra (a lição) ao fim da seção
        ## Procedure da skill existente (cria a seção se ausente)."""
        marker = "## Procedure"
        rule = f"- {fact}"
        if marker in current:
            head, sep, tail = current.partition(marker)
            rest = sep + tail
            nxt = rest.find("\n## ")
            insert_at = len(head) + (nxt if nxt != -1 else len(rest))
            if insert_at > 0 and current[insert_at - 1] != "\n":
                insert_at += 1
            proposed = current[:insert_at].rstrip() + "\n" + rule + "\n" + current[insert_at:]
        else:
            proposed = current.rstrip() + "\n\n" + marker + "\n\n" + rule + "\n"
        return "\n".join(difflib.unified_diff(
            current.splitlines(), proposed.splitlines(),
            fromfile="a/SKILL.md", tofile="b/SKILL.md", lineterm=""))

    def _audit_skill_md(self, content: str, name: str) -> Dict[str, Any]:
        """Gate g3/g4 (P3): o SKILL.md candidato passa no auditor REAL
        (scripts/audit_skills.py). Fail-closed: qualquer violação reprova."""
        import importlib.util
        import tempfile

        spec = importlib.util.spec_from_file_location(
            "audit_skills", Path(__file__).resolve().parents[3] / "scripts" / "audit_skills.py")
        assert spec is not None and spec.loader is not None, "auditor do P3 não carregável"
        audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(audit)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "skills"
            d = root / PROMOTION_CATEGORY / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(content, encoding="utf-8")
            result = audit.audit_root(root)
            violations = [v for s in result["skills"] for v in s["violations"]]
        return {"pass": not violations, "violations": len(violations), "details": violations[:5]}

    # ── Montagem das propostas ────────────────────────────────────────────────

    def _provenance_block(self, lesson: Dict[str, Any]) -> Dict[str, Any]:
        meta = lesson["metadata"]
        return {
            "lesson_fact": lesson["fact"],
            "lesson_rel": lesson["rel"],
            "source_sessions": list(meta.get("provenance") or []),
            "confidence": float(meta.get("confidence") or 0.0),
            "extracted_at": str(meta.get("extracted_at") or ""),
        }

    def _build_create_proposal(self, lesson: Dict[str, Any], proposed_name: str, limit: int, pins: Tuple[str, ...]) -> Dict[str, Any]:
        prov = self._provenance_block(lesson)
        meta = lesson["metadata"]
        key = candidate_key(lesson["fact"])
        content = skill_md_content(
            name=proposed_name, lesson=lesson["fact"],
            description=description_from_lesson(lesson["fact"]),
            lesson_key=key,
            source_session=str(meta.get("source_session") or ""),
            confidence=float(meta.get("confidence") or 0.0),
            extracted_at=str(meta.get("extracted_at") or ""),
            provenance=list(meta.get("provenance") or []),
        )
        return {
            "action": "create",
            "skill_name": proposed_name,
            "target_path": f"skills/{PROMOTION_CATEGORY}/{proposed_name}/SKILL.md",
            "skill_md": content,
            "audit": {"pass": None, "violations": -1, "details": []},  # preenchido pelo gate
            "loadout": {"limit": limit, "pins": list(pins), "decision": "deferred_to_loadout_cap"},
            **prov,
        }

    def _build_refine_proposal(self, lesson: Dict[str, Any], target: str, signal: str, diff: str, limit: int, pins: Tuple[str, ...]) -> Dict[str, Any]:
        prov = self._provenance_block(lesson)
        return {
            "action": "refine",
            "skill_name": target,
            "target_skill": target,
            "dedup_signal": signal,
            "diff": diff,
            "audit": {"pass": True, "violations": 0, "details": [],
                      "note": "refine não gera SKILL.md novo; o diff anexa a regra à skill existente"},
            "loadout": {"limit": limit, "pins": list(pins), "decision": "deferred_to_loadout_cap"},
            **prov,
        }

    def _mark_instinct(self, fact: str, skill_name: str) -> None:
        """O instinto da lição (mesmo namespace de hermes_cli/haos_cmd.py:391)
        sai do radar do Ouroboros: promovido para a skill proposta."""
        if self.instinct_store is None:
            return
        from hermes.platform.memory.instincts import InstinctStore  # function-level

        self.instinct_store.mark_promoted(
            self.instinct_project_scope, [InstinctStore.instinct_id_for(fact)], skill_name)

    # ── Execução ──────────────────────────────────────────────────────────────

    def run(self) -> Dict[str, Any]:
        """Roda a promoção sobre as lições canônicas de okf/. Idempotente: uma
        lição já proposta (ou já coberta) nunca gera proposta nova."""
        from agent.skill_utils import get_skill_loadout_limit, get_skill_loadout_pins  # function-level

        counts: Dict[str, int] = {"proposed": 0, "ignored": 0, "deduped": 0, "audit_failed": 0}
        park = self.audited_park()
        limit = get_skill_loadout_limit()
        pins = get_skill_loadout_pins()

        for lesson in self.scan_lessons():
            fact = lesson["fact"]
            if not fact:
                continue
            key = candidate_key(fact)
            if self.store.get(key) is not None:
                continue  # idempotente: já proposta

            meta = lesson["metadata"]
            destination = str(meta.get("destination") or self.router.classify_destination(fact))
            confidence = float(meta.get("confidence") or 0.0)
            ra = assess_lesson_promotion(fact, destination, confidence)
            if ra.action == "ignore":
                counts["ignored"] += 1
                continue

            proposed_name = skill_name_from_lesson(fact)
            target, signal = self.covering_skill(fact, proposed_name, park)
            if target is not None:
                # Dedup no parque auditado: a criação é suprimida quando uma skill
                # cobre a lição (nome/slug/domínio → refine, g5; corpo verbatim →
                # a regra já está ensinada → nada a propor, ruído).
                counts["deduped"] += 1
                if signal == "verbatim":
                    continue
                diff = self._refine_diff(park[target]["content"], fact)
                proposal = self._build_refine_proposal(lesson, target, signal, diff, limit, pins)
            else:
                proposal = self._build_create_proposal(lesson, proposed_name, limit, pins)
                audit = self._audit_skill_md(proposal["skill_md"], proposed_name)
                proposal["audit"] = audit
                if not audit["pass"]:
                    counts["audit_failed"] += 1
                    continue  # fail-closed: sem gate verde, sem proposta

            self.store.add(key, proposal)
            counts["proposed"] += 1
            self._mark_instinct(fact, proposal["skill_name"])

        return counts