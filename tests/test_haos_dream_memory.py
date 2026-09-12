"""Contract tests for HAOS Dream Routine (consolidation + git audit store).

P11 — gate de staging: a lição só chega à árvore canônica (<home>/okf) quando
PROMOVIDA por recorrência entre sessões (reforço no InstinctStore eleva a
confiança até o limiar do MemoryRouter); abaixo disso o candidato fica
PERSISTIDO como ``pending`` no MemoryStagingStore, com proveniência e confiança.
"""

from pathlib import Path
import shutil
import time

from hermes.platform.memory.dream import DreamConsolidator, DreamGitStore
from hermes.platform.memory.instincts import InstinctStore, CONFIDENCE_PROMOTION_THRESHOLD
from hermes.platform.context.memory.staging import PROMOTED, PENDING, candidate_key
from hermes_state import SessionDB

# Primeira mensagem de usuário de cada sessão: < 60 chars (não é truncada pelo
# _shape_preview) e sem "." — a lição extraída é exatamente esta frase.
LESSON = "Sempre rodar o wrapper de testes antes do commit"


def _new_session(home: Path, sid: str, lesson: str, title: str = "Sessao com licao") -> None:
    db = SessionDB()
    db.ensure_session(session_id=sid, source="cli", model="gemini-test")
    db.set_session_title(sid, title)
    db.append_message(sid, "user", lesson)
    db.append_message(sid, "assistant", "Confirmado.")
    db.close()


def test_dream_git_store_init_and_commit(tmp_path: Path):
    memory_root = tmp_path / "memory"
    store = DreamGitStore(memory_root)

    # Init
    created = store.init_if_needed()
    assert created is True
    assert (memory_root / ".git").is_dir()

    # Commit when clean should return None
    assert store.commit_changes() is None

    # Write a new memory file and commit
    test_file = memory_root / "test_memory.md"
    test_file.write_text("# Knowledge\nSome facts learned.\n", encoding="utf-8")

    info = store.commit_changes("dream: add test memory")
    assert info is not None
    assert len(info.sha) >= 7
    assert "dream: add test memory" in info.message

    # List commits
    commits = store.list_commits()
    assert len(commits) >= 2
    assert any("dream: add test memory" in c["subject"] for c in commits)


def test_dream_consolidator_runs_and_records_cursor(tmp_path: Path, monkeypatch):
    """Contrato antigo preservado: dry-run conta sem escrever; run grava cursor e commit."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    _new_session(home, "20260908_test_dream_session", "Qual o plano de contingência?")

    consolidator = DreamConsolidator(hermes_home=home)
    assert consolidator.get_cursor() == 0.0

    # Dry-run
    dry_res = consolidator.run_dream(dry_run=True)
    assert dry_res["status"] == "dry_run"
    assert dry_res["consolidated_count"] == 1
    assert consolidator.get_cursor() == 0.0

    # Real run
    res = consolidator.run_dream(dry_run=False)
    assert res["status"] == "success"
    assert res["consolidated_count"] == 1
    assert res["commit"] is not None
    assert consolidator.get_cursor() > 0.0

    # Second run without new sessions should be idle
    idle_res = consolidator.run_dream(dry_run=False)
    assert idle_res["status"] == "idle"
    assert idle_res["consolidated_count"] == 0


def test_dream_lição_de_baixa_confiança_fica_pendente_sem_entrar_no_okf(tmp_path: Path, monkeypatch):
    """P11: uma lição vista em UMA sessão NÃO entra na árvore canônica — vira staging pending.

    O código antigo escrevia lesson_*.md direto em <home>/okf nesta situação; o
    contrato novo (promoção por recorrência) exige árvore vazia e candidato
    PERSISTIDO com proveniência e confiança inicial.
    """
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    sid = "20260908_staging_01"
    _new_session(home, sid, LESSON)

    consolidator = DreamConsolidator(hermes_home=home)
    assert consolidator.okf_dir == home / "okf"

    res = consolidator.run_dream(dry_run=False)
    assert res["status"] == "success"
    assert res["consolidated_count"] == 1
    assert res["staged_count"] == 1
    assert res["promoted_count"] == 0

    # Nada na árvore canônica (e nada na subpasta antiga memory/okf)
    assert list((home / "okf").glob("*.md")) == []
    assert not (home / "memory" / "okf").exists()

    # O candidato está PERSISTIDO como pending, com proveniência e confiança inicial
    key = candidate_key(LESSON)
    rec = consolidator.staging_store.get(key)
    assert rec is not None
    assert rec["status"] == PENDING
    assert rec["confidence"] == 0.3
    assert rec["provenance"] == [f"session://{sid}"]
    pending_keys = [r["key"] for r in consolidator.staging_store.list_pending()]
    assert pending_keys == [key]


def test_dream_e2e_lição_promovida_só_após_reforço_entre_sessões(tmp_path: Path, monkeypatch):
    """E2E real (HERMES_HOME temporário): a mesma lição em N sessões só chega ao OKF
    canônico quando a recorrência eleva a confiança do instinto ao limiar.

    Escada do InstinctStore: 0.3 -> 0.5 -> 0.7 (pendente) -> 0.9 (promovida).
    """
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    consolidator = DreamConsolidator(hermes_home=home)
    expected_conf = {1: 0.3, 2: 0.5, 3: 0.7, 4: 0.9}
    sids = []

    for i in range(1, 5):
        sid = f"20260908_e2e_{i:02d}"
        sids.append(sid)
        _new_session(home, sid, LESSON, title=f"Sessao com licao {i:02d}")
        time.sleep(0.01)  # started_at estritamente crescente para o cursor do dream

        res = consolidator.run_dream(dry_run=False)
        assert res["status"] == "success"
        assert res["consolidated_count"] == 1

        conf = expected_conf[i]
        if conf >= CONFIDENCE_PROMOTION_THRESHOLD:
            assert res["promoted_count"] == 1, f"run {i}: deveria promover (conf {conf})"
            assert res["staged_count"] == 0
        else:
            assert res["promoted_count"] == 0, f"run {i}: ainda não deveria promover (conf {conf})"
            assert res["staged_count"] == 1

    # Após o reforço suficiente: exatamente UMA lição canônica em <home>/okf
    lessons = list((home / "okf").glob("lesson_*.md"))
    assert len(lessons) == 1, f"esperado 1 documento okf, achei {len(lessons)}"
    doc_text = lessons[0].read_text(encoding="utf-8")
    assert LESSON in doc_text
    assert "source_session" in doc_text
    assert "destination" in doc_text
    assert not (home / "memory" / "okf").exists()

    # Registro de staging promovido (audit trail), não apagado
    key = candidate_key(LESSON)
    rec = consolidator.staging_store.get(key)
    assert rec is not None
    assert rec["status"] == PROMOTED
    assert rec["promoted_path"] == str(lessons[0])
    assert rec["provenance"] == [f"session://{sid}" for sid in sids]

    # O instinto reforçado está visível para o consumidor de hermes_cli/haos_cmd.py:391
    # (get_eligible_promotions("default")) — mesma confiança e contagem.
    istore = InstinctStore(home / "memory" / "instincts")
    instincts = istore.load_instincts("default")
    ins = instincts[InstinctStore.instinct_id_for(LESSON)]
    assert ins.confidence == 0.9
    assert ins.occurrences == 4
    assert len(istore.get_eligible_promotions("default")) == 1


def test_dream_reforço_é_por_sessão_não_por_repasse(tmp_path: Path, monkeypatch):
    """Reprocessar a MESMA sessão (cursor zerado) não reforça: recorrência é ENTRE sessões."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    sid = "20260908_reforco_01"
    _new_session(home, sid, LESSON)

    consolidator = DreamConsolidator(hermes_home=home)
    res1 = consolidator.run_dream(dry_run=False)
    assert res1["staged_count"] == 1

    key = candidate_key(LESSON)
    rec1 = consolidator.staging_store.get(key)
    assert rec1["confidence"] == 0.3
    assert rec1["provenance"] == [f"session://{sid}"]

    # Reprocessa a mesma sessão: nada de novo, nenhum reforço
    consolidator.set_cursor(0.0)
    res2 = consolidator.run_dream(dry_run=False)
    assert res2["consolidated_count"] == 0

    rec2 = consolidator.staging_store.get(key)
    assert rec2["confidence"] == 0.3
    assert rec2["provenance"] == [f"session://{sid}"]

    istore = InstinctStore(home / "memory" / "instincts")
    instincts = istore.load_instincts("default")
    ins = instincts[InstinctStore.instinct_id_for(LESSON)]
    assert ins.occurrences == 1
    assert ins.confidence == 0.3


def test_dream_lição_conflitante_fica_pendente_e_não_promove(tmp_path: Path, monkeypatch):
    """Conflito com lição canônica: candidato de alta confiança NÃO entra no OKF — volta pendente.

    Prova que o ramo de conflito do dream (nada é descartado) é alcançável de verdade:
    a lição B é o oposto semântico da lição A já canônica; com confiança >= 0.85 o
    MemoryConsolidator detecta o conflito via get_existing_facts e o dream re-estagia.
    """
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    lesson_a = "Reusable workflow procedure to deploy with rollback"
    lesson_b = "Reusable workflow procedure to deploy without rollback"

    consolidator = DreamConsolidator(hermes_home=home)
    for i in range(1, 5):
        _new_session(home, f"20260908_conf_a{i:02d}", lesson_a, title=f"Conf A {i:02d}")
        time.sleep(0.01)
        consolidator.run_dream(dry_run=False)
    assert len(list((home / "okf").glob("lesson_*.md"))) == 1  # A promovida

    last = None
    for i in range(1, 5):
        _new_session(home, f"20260908_conf_b{i:02d}", lesson_b, title=f"Conf B {i:02d}")
        time.sleep(0.01)
        last = consolidator.run_dream(dry_run=False)
        assert last["status"] == "success"

    # Run 4 de B (confiança 0.9): o conflito com A impede a promoção, nada é descartado
    assert last["promoted_count"] == 0
    assert last["staged_count"] == 1
    assert len(list((home / "okf").glob("lesson_*.md"))) == 1  # B não entrou

    key = candidate_key(lesson_b)
    rec = consolidator.staging_store.get(key)
    assert rec is not None
    assert rec["status"] == PENDING
    assert rec["gate"] == "conflict_with_canonical"
    assert rec["confidence"] == 0.9
    assert len(rec["provenance"]) == 4


def test_dream_lição_já_canônica_não_é_duplicada_após_perda_de_estado(tmp_path: Path, monkeypatch):
    """Estado parcial: se staging/instintos forem perdidos mas a lição já está canônica,
    re-promover NÃO duplica o documento na árvore (idempotência por conteúdo)."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    consolidator = DreamConsolidator(hermes_home=home)
    for i in range(1, 5):
        _new_session(home, f"20260908_dup_a{i:02d}", LESSON, title=f"Dup A {i:02d}")
        time.sleep(0.01)
        consolidator.run_dream(dry_run=False)
    assert len(list((home / "okf").glob("lesson_*.md"))) == 1

    # Perda de estado do pipeline (staging + instintos), mantendo a árvore canônica
    shutil.rmtree(home / "memory" / "staging")
    shutil.rmtree(home / "memory" / "instincts")

    consolidator2 = DreamConsolidator(hermes_home=home)
    last = None
    for i in range(1, 5):
        _new_session(home, f"20260908_dup_b{i:02d}", LESSON, title=f"Dup B {i:02d}")
        time.sleep(0.01)
        last = consolidator2.run_dream(dry_run=False)
        assert last["status"] == "success"

    # Reforço reconstruído promove de novo — mas a árvore continua com UMA lição
    assert last["promoted_count"] == 1
    assert len(list((home / "okf").glob("lesson_*.md"))) == 1


def test_dream_dry_run_não_escreve_nada(tmp_path: Path, monkeypatch):
    """dry_run=True não altera a árvore do HERMES_HOME: sem okf, staging, instintos ou cursor."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    _new_session(home, "20260908_dryrun_01", LESSON)

    consolidator = DreamConsolidator(hermes_home=home)

    def _snapshot(root: Path):
        return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())

    before = _snapshot(home)
    cursor_before = consolidator.get_cursor()

    res = consolidator.run_dream(dry_run=True)
    assert res["status"] == "dry_run"
    assert res["consolidated_count"] == 1
    assert res["staged_count"] == 0  # dry-run prevê, não persiste

    after = _snapshot(home)
    assert after == before, f"dry_run escreveu algo: {sorted(set(after) - set(before))}"
    assert consolidator.get_cursor() == cursor_before
    assert not (home / "okf").exists()
