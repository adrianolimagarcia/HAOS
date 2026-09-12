"""Contract tests for HAOS Continuous Learning via Instincts (ECC-inspired)."""

from pathlib import Path
from hermes.platform.memory.instincts import InstinctStore, Instinct
from tools.haos_instinct_tool import instincts_tool


def test_instinct_scoring_lifecycle():
    ins = Instinct(
        id="ins-1",
        rule="Use pytest -m unit on small edits",
        category="test",
        project_scope="test_proj",
        confidence=0.3,
        occurrences=1,
    )
    # First positive validation: 0.3 + 0.2 = 0.5
    conf1 = ins.reinforce()
    assert conf1 == 0.5
    assert ins.occurrences == 2

    # Second positive validation: 0.5 + 0.2 = 0.7
    conf2 = ins.reinforce()
    assert conf2 == 0.7
    assert ins.occurrences == 3

    # Third positive validation: 0.7 + 0.2 = 0.9 (eligible for promotion >= 0.8)
    conf3 = ins.reinforce()
    assert conf3 == 0.9

    # Penalize on contradiction: 0.9 - 0.3 = 0.6
    conf4 = ins.penalize()
    assert conf4 == 0.6


def test_instinct_store_scoping_and_promotion(tmp_path: Path):
    store = InstinctStore(root_dir=tmp_path)

    # Record in Project A
    ins_a = store.record_instinct("Regra do Projeto A", category="workflow", project_scope="proj_a")
    assert ins_a.confidence == 0.3

    # Re-recording reinforces
    ins_a2 = store.record_instinct("Regra do Projeto A", category="workflow", project_scope="proj_a")
    assert ins_a2.confidence == 0.5

    # Should not leak into Project B
    instincts_b = store.load_instincts("proj_b")
    assert len(instincts_b) == 0

    # Boost to promotion threshold
    ins_a2.reinforce() # 0.7
    ins_a2.reinforce() # 0.9
    store.save_instincts("proj_a", {ins_a2.id: ins_a2})

    eligible = store.get_eligible_promotions("proj_a")
    assert len(eligible) == 1
    assert eligible[0].id == ins_a2.id

    # Mark as promoted to skill
    store.mark_promoted("proj_a", [ins_a2.id], "skill_proj_a_rules")
    eligible_after = store.get_eligible_promotions("proj_a")
    assert len(eligible_after) == 0


def test_instincts_tool_interaction(tmp_path: Path):
    # Test record action via tool
    res = instincts_tool("record", rule="Sempre compilar antes de testar", category="workflow", project_scope="default")
    assert '"success": true' in res.lower()
    assert '"confidence": 0.3' in res.lower()

    # Test list action
    res_list = instincts_tool("list", project_scope="default")
    assert '"success": true' in res_list.lower()
    assert "sempre compilar antes de testar" in res_list.lower()


def test_instinct_id_for_eh_o_id_usado_pelo_record(tmp_path: Path):
    """Contrato: record_instinct usa exatamente o id determinístico de instinct_id_for."""
    store = InstinctStore(root_dir=tmp_path)
    rule = "Regra unica de teste"
    ins = store.record_instinct(rule, category="workflow", project_scope="proj")
    assert ins.id == InstinctStore.instinct_id_for(rule)

    reforco = store.record_instinct(rule, project_scope="proj")
    assert reforco.id == ins.id  # reforço NÃO cria segundo registro
    assert set(store.load_instincts("proj")) == {ins.id}


def test_peek_confidence_preve_o_proximo_reforco_sem_escrever(tmp_path: Path):
    """peek_confidence é o dry-run do reforço: prevê a confiança pós-reforço sem tocar no disco."""
    store = InstinctStore(root_dir=tmp_path)
    rule = "Sempre validar o comando antes de rodar"

    # Sem ocorrência nenhuma: prevê a confiança inicial
    assert store.peek_confidence(rule, "proj") == 0.3
    # Instanciar o store e fazer peek não cria nem o diretório
    assert not (tmp_path / "proj.json").exists()

    ins = store.record_instinct(rule, project_scope="proj")
    assert ins.confidence == 0.3
    # A partir daqui, o PRÓXIMO reforço daria 0.5
    assert store.peek_confidence(rule, "proj") == 0.5

    before = (tmp_path / "proj.json").read_text(encoding="utf-8")
    store.peek_confidence(rule, "proj")
    after = (tmp_path / "proj.json").read_text(encoding="utf-8")
    assert before == after  # peek não persiste
