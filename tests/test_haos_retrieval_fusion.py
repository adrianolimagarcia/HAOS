"""F1 (SELF_INDEX_PLAN §Fase 1) — IDF na passada léxica + desempate explícito.

RED-on-base obrigatório (base = 0.21.84). Hoje a passada léxica pontua por
CONTAGEM de tokens (`matches / len(tokens)`): dois documentos que casam com o
mesmo número de tokens da query EMPATAM, e o empate é resolvido por
`created_at DESC` — artefato invisível do sort estável sobre a ordem SQL, não
relevância. Como o corte da lista é `limit * 3`, um empate decidido por
recência também DECIDE QUEM SOBREVIVE AO CORTE, e a fusão RRF perde o voto de
um candidato que o BM25 colocou em #1.

T1/T2 afirmam RELAÇÕES entre dois documentos (não valores congelados):
  T1 — sobreposição léxica idêntica (1 token cada): o token RARO decide a
       ordem da lista léxica, não a recência de indexação.
  T2 — alvo presente no BM25 não pode ser EXCLUÍDO da fusão por truncamento
       da outra lista (o cutoff `limit*3` é mantido; o desempate explícito
       `-bm25_rank` é o que o traz de volta para dentro do corte).

E2E com `HERMES_HOME` temporário e schema real do SQLite (``RAGFlowStore``),
sem mock. ``created_at`` é fixado por UPDATE direto na tabela relacional para
tornar o desempate do base determinístico (sem depender da resolução do
relógio entre duas chamadas) — é a PREMISSA do teste, não o comportamento
afirmado.

Dois cuidados de projeto que, se violados, fariam o teste passar/errar pelo
motivo errado:
  * o FTS5 indexa `doc_path`, `header_path` e `content` — os mesmos campos do
    blob léxico — então os CAMINHOS dos docs de teste não podem conter os
    tokens da query (senão o BM25 também casa e confunde a afirmação);
  * o chunker tem `min_chars=60`: corpo mais curto não gera chunk e o doc fica
    invisível ao índice em silêncio. Todo corpo abaixo é estendido com um
    preenchimento que não contém nenhum token da query.

Nenhum teste lê texto de código-fonte; nenhum é change-detector.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hermes.platform.memory.ragflow_engine import RAGFlowStore

# Tokens sintéticos: sem colisão de substring entre si, com os caminhos usados
# abaixo, ou com o preenchimento.
RARE = "zqxvarejao"   # df = 1 chunk  -> idf alto
COMMON = "orbital"    # df = muitos   -> idf baixo

# Preenchimento: garante >= min_chars (60) do chunker sem introduzir tokens.
PAD = "conteudo de preenchimento para ultrapassar o minimo do chunker"

TARGET = "wiki/diario-2026-09-01.md"


def _index(store: RAGFlowStore, db: Path, doc_path: str, body: str, created_at: float) -> None:
    """Indexa um doc e fixa `created_at` (determinismo do desempate do base)."""
    assert store.index_document(doc_path, f"{body} {PAD}") >= 1, f"{doc_path} não gerou chunk"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE haos_rag_chunks SET created_at = ? WHERE doc_path = ?",
            (created_at, doc_path),
        )
        conn.commit()


def _paths_of(store: RAGFlowStore, db: Path, chunk_ids: list[str]) -> list[str]:
    with sqlite3.connect(db) as conn:
        out = []
        for cid in chunk_ids:
            row = conn.execute(
                "SELECT doc_path FROM haos_rag_chunks WHERE id = ?", (cid,)
            ).fetchone()
            assert row is not None, f"chunk {cid} sumiu do índice"
            out.append(row[0])
    return out


@pytest.fixture
def store(tmp_path: Path):
    db = tmp_path / "rag.db"
    return RAGFlowStore(db), db


# ── T1 — raridade decide a ordem, não recência ───────────────────────────────

def test_t1_rare_token_decides_lexical_order_not_created_at(store) -> None:
    """Alvo (token RARO, o MAIS ANTIGO) vs decoys (token COMUM, mais novos),
    todos com a MESMA sobreposição léxica: 1 token da query cada.

    No base os dois pontuam idêntico (`1/2`) e o empate cai em `created_at
    DESC`: um decoy abre a lista. O contrato exige que a RARIDADE do token
    decida — o alvo tem de abrir a lista léxica.
    """
    rag, db = store
    query = f"{RARE} {COMMON}"

    _index(rag, db, TARGET, f"stats do dia: {RARE} registrado", 1000.0)
    for i in range(2, 7):
        _index(
            rag, db,
            f"wiki/diario-2026-09-{i:02d}.md",
            f"stats do dia: {COMMON} estavel",
            2000.0 + i,
        )

    _fts_ranked, lexical_ranked = rag.rank_lists(query, limit=5)

    assert lexical_ranked, "a passada léxica não devolveu candidatos"
    winner = _paths_of(rag, db, [lexical_ranked[0][0]])[0]
    assert winner == TARGET, (
        "o token raro deve decidir a ordem da lista léxica; venceu "
        f"{winner} — empate resolvido por created_at?"
    )
    # Relação (não snapshot): o alvo não reaparece atrás de si mesmo.
    assert TARGET not in _paths_of(rag, db, [cid for cid, _ in lexical_ranked[1:]])


# ── T2 — alvo do BM25 não pode ser cortado da fusão ─────────────────────────

def test_t2_bm25_present_target_not_excluded_by_lexical_truncation(store) -> None:
    """Com `limit=2` o cutoff léxico é `limit*3 = 6` e há 8 candidatos empatados.

    O alvo (token RARO, `created_at` mais antigo) perde o empate no base e fica
    FORA do corte: a fusão recebe dele só o voto do BM25 (onde ele é #1),
    enquanto cada decoy vota nas duas listas e passa na frente. O contrato: um
    candidato presente no BM25 não pode ser excluído da fusão pelo truncamento
    da outra lista — o desempate explícito por posição no BM25 o traz de volta
    para dentro do cutoff.
    """
    rag, db = store
    query = f"{RARE} {COMMON}"

    _index(rag, db, "wiki/alvo.md", f"linha de stats: {RARE}", 1000.0)
    for i in range(7):
        _index(rag, db, f"wiki/decoy-{i}.md", f"linha de stats: {COMMON} {i}", 2000.0 + i)

    fts_ranked, lexical_ranked = rag.rank_lists(query, limit=2)

    # Premissa verificável: o alvo está no BM25, mas o corte léxico do base o
    # deixou de fora — é exatamente isso que a fusão paga.
    assert "wiki/alvo.md" in _paths_of(rag, db, [cid for cid, _ in fts_ranked]), (
        "premissa violada: o alvo deveria estar na lista BM25"
    )
    lexical_paths = _paths_of(rag, db, [cid for cid, _ in lexical_ranked])
    assert len(lexical_paths) < 8, "cutoff não truncou nada; premissa do teste ausente"

    results = rag.hybrid_search(query, limit=2)

    assert results, "hybrid_search não devolveu nada"
    assert results[0].doc_path == "wiki/alvo.md", (
        "alvo ranqueado #1 pelo BM25 foi excluído da fusão pelo truncamento "
        f"da lista léxica; primeiro resultado: {results[0].doc_path}"
    )
