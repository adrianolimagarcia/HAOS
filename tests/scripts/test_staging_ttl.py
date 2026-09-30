"""Contratos do TTL do staging (scripts/staging_ttl.py) e do marcador de
legadas OKF (scripts/okf_mark_legacy.py).

Por que subprocess em vez de import: os scripts sao operacionais e vivem em
scripts/ como arquivos soltos; o contrato que importa e o comportamento de
linha de comando (dry-run por padrao, --apply, --staging-dir/--licoes-dir)
contra um diretorio isolado em tmp_path — exatamente o fluxo que o nightly
executa. Um import direto testaria a funcao, nao o instrumento.

Contratos testados (cada um existe por um risco concreto):
  - arquivamento: so pending + conf < limiar + mais velho que TTL sai do
    indice; promoted, conf >= limiar, jovem e sem-timestamp ficam (o
    sem-timestamp e falha fechada: sem data nao ha prova de velhice).
  - nunca deletar: o registro vai INTEIRO para o archive jsonl (append-only,
    com archived_at); nada desaparece do sistema.
  - journal: 1 linha por arquivamento com op='ttl_archive' no formato
    existente {seq, op, key, record, ts} — recuperacao via delta continua
    lendo essas linhas.
  - idempotencia: segunda passada nao encontra nada a fazer.
  - dry-run nao escreve: sem --apply o indice fica byte-identico.
  - marcador OKF: contract: v0.1/v0.2 conforme as 4 secoes canonicas;
    chave ja existente nunca e tocada; corpo preservado byte a byte;
    segunda passada = 0 pendencias.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TTL_SCRIPT = ROOT / "scripts" / "staging_ttl.py"
MARK_SCRIPT = ROOT / "scripts" / "okf_mark_legacy.py"

DAY = 86400.0


def run_script(script: Path, *args: str) -> subprocess.CompletedProcess:
    # HERMES_HOME/HAOS_HOME para o tmp: o script nunca pode tocar na home real
    # (conftest ja isola, mas o contrato e do proprio script).
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, check=False,
    )


def make_record(key: str, status: str, confidence: float, age_days: float,
                *, seen: bool = True, created: bool = True) -> dict:
    now = time.time()
    ts = now - age_days * DAY
    rec = {
        "key": key,
        "fact": f"fato {key}",
        "status": status,
        "confidence": confidence,
        "proposed_destination": "working",
        "provenance": [f"session://{key}"],
        "source_uri": f"session://{key}",
        "scope": "project",
        "gate": "below_confidence_threshold",
        "promoted_path": None,
    }
    if created:
        rec["created_at"] = ts
    if seen:
        rec["last_seen_at"] = ts
    return rec


def write_staging(staging_dir: Path, records: dict) -> None:
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "pending_candidates.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def read_staging(staging_dir: Path) -> dict:
    return json.loads((staging_dir / "pending_candidates.json").read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def fixture_records() -> dict:
    return {
        "oldlow": make_record("oldlow", "pending", 0.3, 120),      # arquivar
        "oldprom": make_record("oldprom", "promoted", 0.3, 200),   # fica: promoted
        "oldhigh": make_record("oldhigh", "pending", 0.9, 200),    # fica: conf alta
        "oldedge": make_record("oldedge", "pending", 0.5, 200),    # fica: conf == limiar
        "newlow": make_record("newlow", "pending", 0.3, 10),       # fica: jovem
        # fica: sem NENHUM timestamp (falha fechada — sem data nao ha prova
        # de velhice; o fallback created_at so vale quando o campo existe)
        "nods": make_record("nods", "pending", 0.3, 200, seen=False, created=False),
        # fica: sem last_seen mas created_at recente (fallback pelo campo que existe)
        "onlycreated": make_record("onlycreated", "pending", 0.3, 10, seen=False),
    }


# ── staging_ttl.py ───────────────────────────────────────────────────────────


def test_ttl_dry_run_nao_escreve(tmp_path):
    staging = tmp_path / "staging"
    records = fixture_records()
    write_staging(staging, records)
    before = (staging / "pending_candidates.json").read_bytes()

    r = run_script(TTL_SCRIPT, "--staging-dir", str(staging))
    assert r.returncode == 0, r.stderr
    assert "arquivadas: 1" in r.stdout  # dry-run reporta o que faria
    assert (staging / "pending_candidates.json").read_bytes() == before
    assert not (staging / "pending_candidates.archive.jsonl").exists()
    assert not (staging / "pending_candidates.delta.jsonl").exists()


def test_ttl_arquiva_so_pending_baixa_conf_velho(tmp_path):
    staging = tmp_path / "staging"
    write_staging(staging, fixture_records())

    r = run_script(TTL_SCRIPT, "--staging-dir", str(staging), "--apply")
    assert r.returncode == 0, r.stderr
    remaining = read_staging(staging)
    assert "oldlow" not in remaining
    # Cada preservado existe por uma clausula do filtro; a ausencia de oldlow
    # nao pode virar "arquivou tudo".
    assert set(remaining) == {"newlow", "nods", "oldedge", "oldhigh", "oldprom", "onlycreated"}


def test_ttl_arquivo_e_perda_zero_e_append_only(tmp_path):
    staging = tmp_path / "staging"
    records = fixture_records()
    write_staging(staging, records)

    r = run_script(TTL_SCRIPT, "--staging-dir", str(staging), "--apply")
    assert r.returncode == 0, r.stderr
    archive = read_jsonl(staging / "pending_candidates.archive.jsonl")
    assert len(archive) == 1
    entry = archive[0]
    assert entry["key"] == "oldlow"
    assert isinstance(entry["archived_at"], float)
    # O registro vai INTEIRO: nada do original se perde no arquivamento.
    for field, value in records["oldlow"].items():
        assert entry[field] == value

    # append-only: um segundo arquivamento ACRESCENTA linha, nao reescreve.
    more = read_staging(staging)
    more["oldlow2"] = make_record("oldlow2", "pending", 0.3, 150)
    write_staging(staging, more)
    r2 = run_script(TTL_SCRIPT, "--staging-dir", str(staging), "--apply")
    assert r2.returncode == 0, r2.stderr
    archive2 = read_jsonl(staging / "pending_candidates.archive.jsonl")
    assert len(archive2) == 2
    assert {e["key"] for e in archive2} == {"oldlow", "oldlow2"}


def test_ttl_journal_linha_por_arquivamento_no_formato_existente(tmp_path):
    staging = tmp_path / "staging"
    write_staging(staging, fixture_records())

    r = run_script(TTL_SCRIPT, "--staging-dir", str(staging), "--apply")
    assert r.returncode == 0, r.stderr
    journal = read_jsonl(staging / "pending_candidates.delta.jsonl")
    assert len(journal) == 1
    line = journal[0]
    # Formato existente do jornal (P12): exatamente estas 5 chaves.
    assert set(line) == {"seq", "op", "key", "record", "ts"}
    assert line["op"] == "ttl_archive"
    assert line["key"] == "oldlow"
    assert line["seq"] == 1
    assert line["record"]["key"] == "oldlow"
    assert line["record"]["status"] == "archived"
    # recover_from_delta do store exige a chave do contrato no record.
    for k in ("key", "fact", "confidence", "provenance", "status"):
        assert k in line["record"]


def test_ttl_idempotencia_segunda_passada_zero(tmp_path):
    staging = tmp_path / "staging"
    write_staging(staging, fixture_records())

    r1 = run_script(TTL_SCRIPT, "--staging-dir", str(staging), "--apply")
    assert r1.returncode == 0, r1.stderr
    snap_after_first = (staging / "pending_candidates.json").read_bytes()

    r2 = run_script(TTL_SCRIPT, "--staging-dir", str(staging), "--apply")
    assert r2.returncode == 0, r2.stderr
    assert "arquivadas: 0" in r2.stdout
    # Nada foi tocado na segunda passada: snapshot byte-identico e journal
    # com a mesma contagem de linhas.
    assert (staging / "pending_candidates.json").read_bytes() == snap_after_first
    assert len(read_jsonl(staging / "pending_candidates.delta.jsonl")) == 1


def test_ttl_sem_snapshot_nao_falha(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    r = run_script(TTL_SCRIPT, "--staging-dir", str(staging), "--apply")
    assert r.returncode == 0, r.stderr
    assert "arquivadas: 0" in r.stdout


# ── okf_mark_legacy.py ───────────────────────────────────────────────────────

CONFORME = """---
title: "Conforme"
type: lesson
---

# Lição: conforme

## 1. O Princípio Abstrato Universal (A Regra que Governa Qualquer Sistema)
x

## 2. A Heurística de Diagnóstico Universal (Como Detectar Sem Adivinhar)
y

## 3. Matriz de Cenários e Soluções (Aplicações Práticas)
z

## 4. Estudo de Caso Determinístico / Evidência Histórica
w
"""

LEGADA = """---
title: "Legada"
type: lesson
confidence: 0.8
---

# Lição: legada

Corpo qualquer com evidência.
"""


def test_marcador_classifica_e_marca(tmp_path):
    licoes = tmp_path / "licoes"
    licoes.mkdir()
    (licoes / "conforme.md").write_text(CONFORME, encoding="utf-8")
    (licoes / "legada.md").write_text(LEGADA, encoding="utf-8")
    (licoes / "sem_fm.md").write_text("# Sem frontmatter\n\nCorpo.\n", encoding="utf-8")
    # YAML malformado pre-existente (escape invalido): tem de sobreviver.
    (licoes / "malformada.md").write_text(
        '---\ntitle: "O "script" roda as 04:30"\ntype: lesson\n---\n\n# corpo\n',
        encoding="utf-8")
    (licoes / "fila.done").write_text("marcador de fila", encoding="utf-8")

    r = run_script(MARK_SCRIPT, "--licoes-dir", str(licoes), "--apply")
    assert r.returncode == 0, r.stderr
    assert "aplicados: 4" in r.stdout  # fila.done nao e licao; conforme vira v0.2

    assert "contract: v0.2" in (licoes / "conforme.md").read_text(encoding="utf-8")
    assert "contract: v0.1" in (licoes / "legada.md").read_text(encoding="utf-8")
    assert "contract: v0.1" in (licoes / "sem_fm.md").read_text(encoding="utf-8")
    assert "contract: v0.1" in (licoes / "malformada.md").read_text(encoding="utf-8")
    # marcador de fila intocado
    assert (licoes / "fila.done").read_text(encoding="utf-8") == "marcador de fila"


def test_marcador_corpo_intacto_e_nao_toca_marcada(tmp_path):
    import yaml

    licoes = tmp_path / "licoes"
    licoes.mkdir()
    (licoes / "legada.md").write_text(LEGADA, encoding="utf-8")
    (licoes / "marcada.md").write_text(
        '---\ntitle: "X"\ncontract: v0.9\n---\n\ncorpo\n', encoding="utf-8")
    before_legada = (licoes / "legada.md").read_text(encoding="utf-8")
    before_marcada = (licoes / "marcada.md").read_bytes()

    r = run_script(MARK_SCRIPT, "--licoes-dir", str(licoes), "--apply")
    assert r.returncode == 0, r.stderr

    after = (licoes / "legada.md").read_text(encoding="utf-8")
    # Corpo (pos-frontmatter) byte-identico: a unica mudanca e a linha do
    # frontmatter — diff de corpo = zero.
    body_before = before_legada.split("---\n", 2)[2]
    body_after = after.split("---\n", 2)[2]
    assert body_after == body_before
    # Frontmatter continua parseavel e nao corrompeu as chaves vizinhas.
    fm = yaml.safe_load(after.split("---\n")[1])
    assert fm["title"] == "Legada" and fm["confidence"] == 0.8 and fm["contract"] == "v0.1"
    # Chave contract ja existente nunca e reescrita (mesmo valor absurdo).
    assert (licoes / "marcada.md").read_bytes() == before_marcada


def test_marcador_idempotencia_segunda_passada_zero(tmp_path):
    licoes = tmp_path / "licoes"
    licoes.mkdir()
    (licoes / "conforme.md").write_text(CONFORME, encoding="utf-8")
    (licoes / "legada.md").write_text(LEGADA, encoding="utf-8")

    r1 = run_script(MARK_SCRIPT, "--licoes-dir", str(licoes), "--apply")
    assert r1.returncode == 0, r1.stderr
    snap = {p.name: p.read_bytes() for p in licoes.glob("*.md")}

    r2 = run_script(MARK_SCRIPT, "--licoes-dir", str(licoes))
    assert r2.returncode == 0, r2.stderr
    assert "0 pendencia(s)" in r2.stdout
    assert "ja_marcada=2" in r2.stdout
    assert {p.name: p.read_bytes() for p in licoes.glob("*.md")} == snap
