"""Testes de contrato do P5 — governança de memória (backlog HAOS Etapa 3).

Três políticas, cada uma com contrato verificável (nenhum teste lê texto de
código-fonte; nenhum é change-detector):

  * PROVENIÊNCIA: toda lição canônica escrita pelo dream carrega
    source_session, destination, confidence, provenance, extracted_at, model e
    evidence_span (o trecho literal do transcript que a originou), além do
    skill_afetada determinístico (P2/MAP);
  * TTL: candidato pending sem reforço em 30 dias (ou com valid_to vencido) é
    DEMOVIDO para "expired" — nunca apagado (trilha de auditoria); lições
    canônicas com valid_to vencido são reportadas e demovidas (obsolete: true);
  * INTEGRIDADE: baseline SHA-256 dos arquivos canônicos — mudança fora de
    banda (sem escrita registrada no mesmo turno) falha o verify; JSON de
    staging corrompido e documento canônico sem conteúdo mínimo são violações.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes.platform.context.memory.candidate import MemoryCandidate
from hermes.platform.context.memory.staging import (
    PENDING,
    MemoryStagingStore,
    candidate_key,
)
from hermes.platform.memory.dream import DreamConsolidator
from hermes.platform.memory.memory_governance import (
    MEMORY_TTL_DAYS,
    demote_obsolete_lessons,
    list_obsolete_lessons,
    MemoryIntegrityChecker,
)
from hermes.platform.memory.okf import OKFStore
from hermes_state import SessionDB

LESSON = "Workflow procedure how to deploy with rollback"
LESSON_2 = "Workflow procedure how to restore a broken gateway"


def _new_session(home: Path, sid: str, lesson: str) -> None:
    db = SessionDB()
    db.ensure_session(session_id=sid, source="cli", model="gemini-test")
    db.set_session_title(sid, f"Sessao {sid}")
    db.append_message(sid, "user", lesson)
    db.append_message(sid, "assistant", "Confirmado.")
    db.close()


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


# ── Proveniência por escrita ──────────────────────────────────────────────────

def test_licao_canonica_carrega_proveniencia_completa(tmp_path, monkeypatch):
    """E2E real (HERMES_HOME temporário): a lição promovida pelo dream no OKF
    carrega source_session, extracted_at, model, evidence_span e skill_afetada
    — o contrato de proveniência do P5, não apenas o que o P11 já gravava."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    for i in range(1, 5):  # 0.3 → 0.5 → 0.7 → 0.9 (promove no 4º)
        _new_session(home, f"20260912_gov_{i:02d}", LESSON)
        time.sleep(0.01)
        DreamConsolidator(hermes_home=home).run_dream(dry_run=False)

    docs = list((home / "okf").glob("lesson_*.md"))
    assert len(docs) == 1, f"esperado 1 lição canônica, achei {len(docs)}"

    meta = OKFStore(home / "okf").documents()[0].metadata
    for key in ("source_session", "destination", "confidence", "provenance",
                "extracted_at", "model", "evidence_span", "skill_afetada"):
        assert key in meta, f"proveniência {key!r} ausente no frontmatter da lição"

    assert meta["destination"] == "skill"
    assert meta["confidence"] == 0.9
    assert meta["model"] == "gemini-test"
    assert isinstance(meta["extracted_at"], str) and meta["extracted_at"]
    assert isinstance(meta["evidence_span"], str) and LESSON in meta["evidence_span"]
    assert "session://20260912_gov_01" in meta["provenance"]


def test_staging_ja_guarda_sessao_timestamp_e_confianca(tmp_path):
    """O degrau de staging (P11) já materializa a tríade sessão/timestamp/confiança
    que a proveniência do P5 exige — o contrato fica explícito."""
    store = MemoryStagingStore(tmp_path)
    cand = MemoryCandidate(
        fact=LESSON, source_uri="session://gov-1", confidence=0.3,
        provenance=["session://gov-1"],
    )
    store.stage_candidate(cand, reason="below_confidence_threshold")
    rec = store.get(candidate_key(LESSON))
    for key in ("key", "fact", "source_uri", "confidence", "provenance",
                "status", "created_at", "last_seen_at"):
        assert key in rec, f"candidato staging sem {key!r}"
    assert rec["provenance"] == ["session://gov-1"]
    assert rec["created_at"] <= time.time()
    assert rec["confidence"] == 0.3


# ── TTL: demote determinístico, nunca apagar ──────────────────────────────────

def _age_record(store: MemoryStagingStore, key: str, days: float) -> None:
    data = json.loads(store.index_file.read_text(encoding="utf-8"))
    data[key]["last_seen_at"] = time.time() - days * 86400
    store.index_file.write_text(json.dumps(data), encoding="utf-8")


def test_expire_demove_pending_sem_reforco_em_30_dias(tmp_path):
    from hermes.platform.context.memory.staging import EXPIRED

    store = MemoryStagingStore(tmp_path)
    cand = MemoryCandidate(fact=LESSON, source_uri="session://x", confidence=0.3)
    store.stage_candidate(cand)
    key = candidate_key(LESSON)
    assert store.get(key)["status"] == PENDING

    # 31 dias sem reforço (last_seen_at antigo) → demovido
    _age_record(store, key, 31)
    assert store.expire(now=_now_ts()) == 1
    assert store.get(key)["status"] == EXPIRED

    # Excluído do pendente, mas o registro permanece (trilha de auditoria)
    assert store.list_pending() == []
    assert store.get(key) is not None
    assert store.get(key)["provenance"] == ["session://x"]


def test_expire_respeita_valid_to(tmp_path):
    from hermes.platform.context.memory.staging import EXPIRED

    store = MemoryStagingStore(tmp_path)

    c1 = MemoryCandidate(fact=LESSON, source_uri="session://a", confidence=0.3)
    store.stage_candidate(c1)
    k1 = candidate_key(LESSON)
    r1 = store.get(k1)
    r1["valid_to"] = 1000.0  # vencido

    c2 = MemoryCandidate(fact=LESSON_2, source_uri="session://b", confidence=0.3)
    store.stage_candidate(c2)
    k2 = candidate_key(LESSON_2)
    r2 = store.get(k2)
    r2["valid_to"] = _now_ts() + 999999.0  # vigente

    store._save({k1: r1, k2: r2})
    store.expire(now=_now_ts())

    assert store.get(k1)["status"] == EXPIRED
    assert store.get(k2)["status"] == PENDING


def test_licao_obsoleta_reportada_e_demovida(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    store = OKFStore(home / "okf")
    store.save_document(
        title="Licao antiga",
        content=LESSON,
        doc_type="concept",
        filename="lesson_old.md",
        extra_metadata={"valid_to": 1000.0, "destination": "skill", "confidence": 0.9},
    )

    now = _now_ts()
    obs = list_obsolete_lessons(home / "okf", now=now)
    assert len(obs) == 1
    assert obs[0]["rel"] == "lesson_old.md"

    assert demote_obsolete_lessons(home / "okf", now=now) == 1
    reloaded = OKFStore(home / "okf").documents()[0]
    assert reloaded.metadata.get("obsolete") is True
    assert "demoted_at" in reloaded.metadata

    # Idempotente: lição já demovida não conta de novo
    assert demote_obsolete_lessons(home / "okf", now=now) == 0


def test_politica_ttl_documentada_em_30_dias():
    """A política de obsolescência está declarada (30 dias — corte de ruído que a
    literatura mediu "by roughly half"); é o mesmo número usado pelo expire."""
    assert MEMORY_TTL_DAYS == 30


# ── Integridade: baseline SHA-256, fora de banda falha ────────────────────────

def test_integridade_detecta_mudanca_fora_de_banda(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    checker = MemoryIntegrityChecker(home=home)

    # Sem arquivos canônicos: nada a checar → limpo
    assert checker.verify() == []

    # Escrita legítima + baseline no mesmo turno → limpo
    store = OKFStore(home / "okf")
    store.save_document(
        title="Licao",
        content=LESSON,
        doc_type="concept",
        filename="lesson_x.md",
        extra_metadata={"destination": "skill", "confidence": 0.9,
                        "extracted_at": "2026-09-12T00:00:00+00:00",
                        "source_session": "session-x"},
    )
    checker.register_write()
    assert checker.verify() == []

    # Adulteração FORA de banda (sem registrar escrita) → violação detectada
    target = home / "okf" / "lesson_x.md"
    tampered = target.read_text(encoding="utf-8").replace(LESSON, LESSON_2)
    target.write_text(tampered, encoding="utf-8")
    violations = checker.verify()
    assert len(violations) == 1, violations
    assert violations[0]["kind"] == "changed"
    assert violations[0]["rel"] == "okf/lesson_x.md"

    # Registrar a escrita legítima (o fluxo de escrita) faz voltar a passar
    checker.register_write()
    assert checker.verify() == []


def test_register_files_fecha_o_falso_positivo_sem_cegar_o_detector(tmp_path, monkeypatch):
    """register_files() registra só o arquivo escrito pela tool.

    Contrato: (1) a escrita legítima deixa de aparecer como "nova sem escrita
    registrada"; (2) um arquivo criado FORA do fluxo continua sendo acusado —
    o que register_write() (baseline inteiro) mascararia.
    """
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    checker = MemoryIntegrityChecker(home=home)
    store = OKFStore(home / "okf")

    store.save_document(title="Licao A", content=LESSON, doc_type="concept", filename="lesson_a.md")
    checker.register_files([home / "okf" / "lesson_a.md"])
    assert checker.verify() == [], "escrita legítima registrada deve passar limpa"

    # Arquivo criado fora do fluxo legítimo: o detector continua funcionando
    intruso = home / "okf" / "lesson_intruso.md"
    intruso.write_text(LESSON, encoding="utf-8")
    violations = checker.verify()
    assert [(v["kind"], v["rel"]) for v in violations] == [("new", "okf/lesson_intruso.md")], violations

    # Registrar OUTRA escrita legítima não pode absorver o intruso no baseline
    store.save_document(title="Licao B", content=LESSON_2, doc_type="concept", filename="lesson_b.md")
    checker.register_files([home / "okf" / "lesson_b.md"])
    restantes = [(v["kind"], v["rel"]) for v in checker.verify()]
    assert restantes == [("new", "okf/lesson_intruso.md")], restantes


def test_register_files_preserva_o_resto_do_baseline(tmp_path, monkeypatch):
    """Registro cirúrgico não apaga entradas anteriores nem a versão do manifest."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    checker = MemoryIntegrityChecker(home=home)
    store = OKFStore(home / "okf")

    store.save_document(title="Licao A", content=LESSON, doc_type="concept", filename="lesson_a.md")
    store.save_document(title="Licao B", content=LESSON_2, doc_type="concept", filename="lesson_b.md")
    checker.register_files([home / "okf" / "lesson_a.md"])
    checker.register_files([home / "okf" / "lesson_b.md"])

    manifest = json.loads((home / "memory" / "integrity" / "baseline.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    assert sorted(manifest["files"]) == ["okf/lesson_a.md", "okf/lesson_b.md"]
    assert checker.verify() == []


def test_integridade_detecta_json_de_staging_corrompido(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    staging = home / "memory" / "staging"
    staging.mkdir(parents=True)
    (staging / "pending_candidates.json").write_text("{json quebrado", encoding="utf-8")

    checker = MemoryIntegrityChecker(home=home)
    violations = checker.validate_json_index()
    assert len(violations) == 1
    assert violations[0]["kind"] == "corrupt_json"


def test_integridade_exige_conteudo_minimo_nos_documentos_canonicos(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    store = OKFStore(home / "okf")
    store.save_document(
        title="Licao vazia", content="", doc_type="concept", filename="lesson_empty.md",
        extra_metadata={"destination": "skill", "confidence": 0.9},
    )

    checker = MemoryIntegrityChecker(home=home)
    violations = checker.validate_canonical_documents()
    assert len(violations) == 1, violations
    assert violations[0]["kind"] == "empty_body"
    assert violations[0]["rel"] == "okf/lesson_empty.md"


def test_run_memory_governance_roda_todas_as_partes(tmp_path, monkeypatch):
    """A governança é um passo único e determinístico no consolidator: TTL +
    integridade, sem falhar em home vazio nem inventar violação."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    consolidator = DreamConsolidator(hermes_home=home)
    res = consolidator.run_memory_governance()

    assert res["status"] == "ok"
    assert res["expired"] == 0
    assert res["obsoleted"] == 0
    assert res["integrity"]["violations"] == []
    assert "out_of_band" in res["integrity"]