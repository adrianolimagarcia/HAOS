"""Testes de contrato do P2 — promoção determinística lição → skill (backlog HAOS Etapa 3).

A promoção produz PROPOSTA (gate g7 do relatório: humano no volante), nunca aplica
no parque: o caminho gera o SKILL.md completo, valida contra o auditor do P3
(scripts/audit_skills.py, o MESMO script do CI) e registra a proveniência em
<home>/memory/skill_proposals/proposals.json. O teto de loadout (P3) decide a
entrada numa sessão; a promoção NÃO pode forçar nada.

Contratos verificados aqui (nenhum lê texto de código-fonte; nenhum é
change-detector — todos relacionam duas peças de dados):
  * critério de promoção: destino determinístico "skill" do MemoryRouter +
    confiança >= 0.85 (o gate canônico do P11) + estrutura mínima da lição;
  * dedup no parque auditado (nome normalizado / slug / corpo verbatim / domínio);
  * o SKILL.md gerado passa no auditor real (audit_root de scripts/audit_skills.py);
  * o registro da promoção preserva a proveniência (sessões, confiança, extração);
  * loadout intocado (nenhum config.yaml criado, pins inalterados, decisão
    registrada como deferred_to_loadout_cap).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from hermes.platform.context.memory.router import MemoryRouter
from hermes.platform.memory.okf import OKFStore
from hermes.platform.memory.skill_promotion import (
    PROMOTION_MIN_CONFIDENCE,
    assess_lesson_promotion,
    description_from_lesson,
    skill_name_from_lesson,
    LessonSkillPromoter,
)

# Lição canônica procedural: o MemoryRouter a classifica como "skill"
# (vocabulário RE_SKILL: workflow/procedure/how to) — a condição de entrada.
LESSON = "Workflow procedure how to deploy with rollback"

_MARKETING = ("powerful", "comprehensive", "seamless", "revolutionary",
              "cutting-edge", "state-of-the-art")


def _write_canonical_lesson(home: Path, fact: str, **meta_overrides) -> Path:
    """Materializa uma lição canônica em <home>/okf (mesmo formato do dream)."""
    store = OKFStore(home / "okf")
    base = {
        "source_session": "session-abc123",
        "session_title": "Sessao com licao",
        "destination": "skill",
        "confidence": 0.9,
        "provenance": ["session://session-abc123", "session://session-def456"],
        "extracted_at": "2026-09-12T10:00:00+00:00",
        "model": "gemini-test",
        "evidence_span": fact,
        "skill_afetada": "deploy-rollback",
    }
    base.update(meta_overrides)
    doc = store.save_document(
        title="Licao da Sessao abc123",
        content=fact,
        doc_type="concept",
        tags=["session", "dream", "auto-extracted", "promoted"],
        filename="lesson_abc123.md",
        extra_metadata=base,
    )
    return doc.filepath


def _skill(root: Path, rel: str, name: str, body: str = "body\n") -> Path:
    """Skill de parque com frontmatter válido (name == dir, auditável)."""
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_text(
        "---\n"
        f"name: {name}\n"
        "description: Faz algo util no dominio.\n"
        "version: 1.0.0\n"
        "author: Test Author\n"
        "license: MIT\n"
        "platforms: [linux]\n"
        "metadata:\n"
        "  hermes:\n"
        "    tags: [test]\n"
        "---\n"
        f"# {name}\n{body}",
        encoding="utf-8",
    )
    return p


# ── Critério de promoção (conteúdo/estrutura + classificação + confiança) ─────

def test_router_classifica_a_licao_procedural_como_skill():
    """O critério é ancorado no roteador determinístico, não em heurística nova."""
    router = MemoryRouter()
    assert router.classify_destination(LESSON) == "skill"
    # Lição genérica (sem vocabulário procedural) NÃO é skill — cai em working.
    assert router.classify_destination("Sempre rodar o wrapper de testes antes do commit") == "working"


def test_criterio_exige_destino_skill_confianca_e_estrutura():
    ok = assess_lesson_promotion(LESSON, "skill", 0.9)
    assert ok.action == "create"
    assert ok.reason

    # Destino diferente de skill pertence ao próprio store (core_user/obsidian/...)
    for destination in ("core_user", "core_agent", "obsidian", "task", "working"):
        assert assess_lesson_promotion(LESSON, destination, 0.9).action == "ignore"

    # Confiança abaixo do gate canônico do P11 (0.85) → não promove
    assert assess_lesson_promotion(LESSON, "skill", 0.84).action == "ignore"

    # Estrutura mínima: fragmento curto demais não vira skill
    assert assess_lesson_promotion("Workflow passo", "skill", 0.9).action == "ignore"

    # O limiar de confiança do critério é o MESMO do MemoryCandidate.is_high_confidence
    assert PROMOTION_MIN_CONFIDENCE == 0.85


# ── Geração determinística do SKILL.md (frontmatter + corpo HARDLINE) ─────────

def test_nome_da_skill_e_slug_deterministico_e_estavel():
    a = skill_name_from_lesson(LESSON)
    b = skill_name_from_lesson(LESSON)
    assert a == b  # determinístico
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]{0,48}", a), a
    # Assuntos distintos geram nomes distintos (sem colisão por acaso)
    other = skill_name_from_lesson("Workflow procedure how to hotfix a broken gateway")
    assert other != a


def test_descricao_respeita_hardline_60_chars_ponto_final():
    desc = description_from_lesson(LESSON)
    assert len(desc) <= 60, f"description {len(desc)} chars"
    assert desc.rstrip().endswith(".")
    for word in _MARKETING:
        assert word not in desc.lower()


def test_skill_gerada_passa_no_auditor_real_do_p3(tmp_path, monkeypatch):
    """E2E com scripts/audit_skills.py: a skill candidata materializada num parque
    temporário tem ZERO violações nas regras do auditor (frontmatter, campos
    obrigatórios, name==dir, description ≤60, tamanho)."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_canonical_lesson(home, LESSON)

    promoter = LessonSkillPromoter(home=home)
    res = promoter.run()
    assert res["proposed"] == 1
    prop = promoter.store.list_proposals()[0]
    assert prop["audit"]["pass"] is True
    assert prop["audit"]["violations"] == 0

    # Auditoria independente: materializa o SKILL.md gerado e roda o auditor real
    import importlib.util
    import sys

    repo = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("audit_skills", repo / "scripts" / "audit_skills.py")
    assert spec is not None and spec.loader is not None
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)

    park = tmp_path / "park"
    d = park / "software-development" / prop["skill_name"]
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(prop["skill_md"], encoding="utf-8")
    result = audit.audit_root(park)
    violations = [v for s in result["skills"] for v in s["violations"]]
    assert violations == [], violations
    assert result["skills"][0]["name"] == prop["skill_name"]


# ── Proposta de criação: conteúdo completo + proveniência registrada ──────────

def test_promoter_propoe_create_com_proveniencia_completa(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_canonical_lesson(home, LESSON)

    promoter = LessonSkillPromoter(home=home)
    res = promoter.run()

    assert res["proposed"] == 1
    assert res["ignored"] == 0

    proposals = promoter.store.list_proposals()
    assert len(proposals) == 1
    prop = proposals[0]
    assert prop["action"] == "create"
    assert prop["skill_name"] == skill_name_from_lesson(LESSON)
    assert prop["status"] == "proposed"

    # Proveniência preservada no registro da promoção
    assert prop["lesson_fact"] == LESSON
    assert set(prop["source_sessions"]) == {"session://session-abc123", "session://session-def456"}
    assert prop["confidence"] == 0.9
    assert prop["extracted_at"] == "2026-09-12T10:00:00+00:00"

    # O SKILL.md completo foi gerado: frontmatter + corpo na ordem HARDLINE,
    # com a lição como conteúdo (nunca texto inventado).
    body = prop["skill_md"]
    assert body.startswith("---\n")
    assert f"name: {prop['skill_name']}" in body
    for section in ("# ", "## When to Use", "## Prerequisites", "## How to Run",
                    "## Procedure", "## Pitfalls", "## Verification"):
        assert section in body, f"seção {section!r} ausente"
    assert LESSON in body


def test_run_e_idempotente(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_canonical_lesson(home, LESSON)

    promoter = LessonSkillPromoter(home=home)
    first = promoter.run()
    second = promoter.run()

    assert first["proposed"] == 1
    assert second["proposed"] == 0  # mesma lição nunca é proposta duas vezes
    assert len(promoter.store.list_proposals()) == 1


# ── Dedup no parque auditado ──────────────────────────────────────────────────

def test_dedup_por_nome_vira_refine_e_nunca_duplica_skill(tmp_path, monkeypatch):
    """Nome idêntico no parque: a criação duplicaria a skill (unique_names do
    auditor) — a ação vira refine na skill existente, nunca um SKILL.md novo."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_canonical_lesson(home, LESSON)

    name = skill_name_from_lesson(LESSON)
    skills_root = tmp_path / "park"
    _skill(skills_root, f"software-development/{name}", name)

    promoter = LessonSkillPromoter(home=home, skills_root=skills_root)
    res = promoter.run()
    # Dedup: a criação foi suprimida (nunca haverá um 2º SKILL.md com o mesmo
    # nome — unique_names do auditor); g5: havendo skill do domínio, vira refine.
    assert res["deduped"] == 1
    assert res["proposed"] == 1
    prop = promoter.store.list_proposals()[0]
    assert prop["action"] == "refine"
    assert prop["target_skill"] == name
    assert "diff" in prop and "+" in prop["diff"]


def test_licao_ja_ensinada_verbatim_nao_gera_proposta(tmp_path, monkeypatch):
    """A regra já existe no corpo de uma skill do parque: nada a propor (ruído)."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_canonical_lesson(home, LESSON)

    skills_root = tmp_path / "park"
    _skill(skills_root, "software-development/coverage", "coverage", body=f"{LESSON}\n")

    promoter = LessonSkillPromoter(home=home, skills_root=skills_root)
    res = promoter.run()
    assert res["proposed"] == 0
    assert res["deduped"] == 1
    assert promoter.store.list_proposals() == []


def test_refine_quando_skill_do_dominio_existe(tmp_path, monkeypatch):
    """g5: havendo skill do domínio (token de assunto compartilhado), a ação é
    refine — o diff propõe acrescentar a regra, sem criar skill nova."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_canonical_lesson(home, LESSON)

    skills_root = tmp_path / "park"
    _skill(skills_root, "software-development/deploy-ops", "deploy-ops",
           body="# deploy-ops\n## Procedure\n1. run checks\n")

    promoter = LessonSkillPromoter(home=home, skills_root=skills_root)
    res = promoter.run()
    assert res["proposed"] == 1
    prop = promoter.store.list_proposals()[0]
    assert prop["action"] == "refine"
    assert prop["target_skill"] == "deploy-ops"
    assert LESSON in prop["diff"]  # a regra entra no diff como conteúdo da lição


# ── Loadout: a promoção não força entrada ─────────────────────────────────────

def test_promocao_nao_forca_entrada_no_loadout(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_canonical_lesson(home, LESSON)

    promoter = LessonSkillPromoter(home=home)
    promoter.run()

    # Nenhum config.yaml é criado (a promoção não escreve config de loadout)
    assert not (home / "config.yaml").exists()

    from agent.skill_utils import get_skill_loadout_limit, get_skill_loadout_pins

    assert get_skill_loadout_pins() == ()  # pins intocados
    assert get_skill_loadout_limit() >= 1  # teto P3 continua vigente

    prop = promoter.store.list_proposals()[0]
    assert prop["loadout"]["decision"] == "deferred_to_loadout_cap"


# ── O instinto reforçado sai do radar do Ouroboros após a proposta ────────────

def test_instinto_marcado_promovido_apos_proposta(tmp_path, monkeypatch):
    """O consumidor de hermes_cli/haos_cmd.py:391 (get_eligible_promotions) para
    de listar a lição depois que ela virou proposta de skill — sem dupla contagem."""
    from hermes.platform.memory.instincts import InstinctStore

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_canonical_lesson(home, LESSON)

    istore = InstinctStore(home / "memory" / "instincts")
    for _ in range(4):  # escada 0.3 → 0.5 → 0.7 → 0.9
        istore.record_instinct(rule=LESSON, category="workflow", project_scope="default")
    assert len(istore.get_eligible_promotions("default")) == 1

    promoter = LessonSkillPromoter(home=home, instinct_store=istore,
                                   instinct_project_scope="default")
    promoter.run()

    assert len(istore.get_eligible_promotions("default")) == 0
    ins = istore.load_instincts("default")[InstinctStore.instinct_id_for(LESSON)]
    assert ins.promoted_to_skill == skill_name_from_lesson(LESSON)


# ── Registro do desfecho (mark_applied) preserva a trilha ─────────────────────

def test_mark_applied_preserva_o_registro(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_canonical_lesson(home, LESSON)

    promoter = LessonSkillPromoter(home=home)
    promoter.run()
    prop = promoter.store.list_proposals()[0]

    assert promoter.store.mark_applied(prop["key"]) is True
    after = promoter.store.get(prop["key"])
    assert after["status"] == "applied"
    assert "applied_at" in after
    assert after["lesson_fact"] == LESSON  # proveniência nunca some
