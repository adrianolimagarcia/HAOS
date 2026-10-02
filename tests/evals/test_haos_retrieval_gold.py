r"""Guardas do harness de bench de recuperação HAOS (F0 do SELF_INDEX_PLAN).

Dois contratos:

1. **Unidade pura** — as funções de derivação do gold (``fold``,
   ``norm_phrase``, ``split_sentences``, ``distinctive_sentence``,
   ``derive_aliases``, ``build_gold``) rodam contra um corpus sintético em
   ``tmp_path``. Cada teste reproduz um defeito REAL observado na primeira
   versão do harness (cabeçalho de template como query, título-UUID
   degenerando a variante content, frase compartilhada virando query
   ambígua, canal 3 com ``\?`` literal, ``**ADRs:**`` sem match). Se um
   guard for removido do harness, o teste correspondente fica vermelho.

2. **Integração** — o bench completo contra o router de produção, pulado
   quando o corpus HAOS não existe na máquina (CI do repo público). O gate
   é a regressão de hit@1/hit@3 contra o baseline AGREGADO versionado —
   nunca o valor exato de um número, sempre ``atual >= baseline``.

O gold derivado (``benchmarks/haos_retrieval_gold.json``) é gitignored:
contém trechos do corpus real. Este arquivo de teste não lê o gold —
regenera tudo em ``tmp_path`` ou usa o harness com ``--build-gold``.
"""

from __future__ import annotations

import functools
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "bench_haos_retrieval.py"
BASELINE = REPO / "benchmarks" / "haos_retrieval_gold.baseline.json"
VAULT = Path("/root/.haos/obsidian_vault")


@functools.lru_cache(maxsize=1)
def _load_harness():
    spec = importlib.util.spec_from_file_location("bench_haos_retrieval", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── unidade: normalização ──────────────────────────────────────────────────

def test_fold_strips_accents_and_case():
    m = _load_harness()
    assert m.fold("Recuperação HAOS") == "recuperacao haos"
    # mesma regra de OKFStore._fold: NFKD + drop de combinantes


def test_norm_phrase_folds_separators():
    m = _load_harness()
    # hífen/espaço/underscore/barra colapsam — foi o bug que fez o guard de
    # `exclude` não casar com títulos de UUID (traço vs espaço).
    assert m.norm_phrase("A_B-C D/E") == "a b c d e"
    assert m.norm_phrase("RCA-Compactação HAOS") == m.norm_phrase("rca compactacao  haos")


def test_content_tokens_drops_stopwords_keeps_numbers():
    m = _load_harness()
    # Colisão de fold deliberada: "nós" (grafo) dobra para "nos", que É
    # stopword PT legítima (contração prep.+art.) em _STOP — o token é
    # descartado por design. Os números distinguem as queries de diario,
    # então a colisão não custa recall no corpus real. Teste registra a
    # limitação em vez de brigar com ela.
    toks = m.content_tokens("o teste de 25597 nós passou")
    assert "25597" in toks and "nos" not in toks
    assert "teste" in toks and "passou" in toks
    assert "de" not in toks and "o" not in toks


# ── unidade: split de frases ───────────────────────────────────────────────

def test_split_sentences_drops_headers_and_short_lines():
    m = _load_harness()
    text = (
        "---\ntitle: X\n---\n"
        "# Memory Note - 73c379bc-1111\n"
        "## Content\n"
        "**Scope:** project | **Confidence:** 1.00\n"
        "A sentença longa distintiva que sobrevive ao filtro de quarenta.\n"
        "curta demais\n"
    )
    sents = m.split_sentences(text)
    assert sents == ["A sentença longa distintiva que sobrevive ao filtro de quarenta."]


def test_split_sentences_splits_on_punctuation_and_newlines():
    m = _load_harness()
    # todas as 3 frases com >=40 chars (o filtro de 40 derruba frases curtas)
    text = ("Primeira frase longa o suficiente para passar. "
            "Segunda frase longa também passa aqui depois do ponto final.\n"
            "Terceira após newline também é frase válida o suficiente.\n")
    assert len(m.split_sentences(text)) == 3


# ── unidade: distinctive_sentence (os 3 guards) ────────────────────────────

def _corpus_counter(m, texts):
    df = m.Counter()
    for t in texts:
        df.update(set(m.content_tokens("\n".join(m.body_lines(t)))))
    return df, len(texts)


def test_distinctive_sentence_prefers_rare_over_boilerplate():
    m = _load_harness()
    shared = "Estatísticas do pipeline de consolidação noturna do sistema.\n"
    unique = "O gateway caiu às 03:12 por expiração do token de renovação.\n"
    doc = f"# Nota\n{shared}{unique}"
    other = f"# Outra\n{shared}"
    df, n = _corpus_counter(m, [doc, other])
    chosen = m.distinctive_sentence(doc, df, n)
    assert "token de renovação" in chosen  # IDF alto vence o cabeçalho repetido


def test_distinctive_sentence_exclude_drops_title_bearing_candidate():
    m = _load_harness()
    # defeito real: H1 "Memory Note - <uuid>" com UUID de IDF altíssimo
    # virava a query content — igual à variante title.
    uuid = "73c379bc-aaaa-bbbb-cccc-dddddddddddd"
    doc = (
        f"# Memory Note - {uuid}\n"
        f"Memory note {uuid} registrou a falha do índice vetorial às 03:12.\n"
        "A falha do índice vetorial ocorreu na renovação do certificado.\n"
    )
    df, n = _corpus_counter(m, [doc])
    title = m.norm_phrase(f"Memory Note - {uuid}")
    chosen = m.distinctive_sentence(doc, df, n, exclude=title)
    assert uuid not in chosen


def test_distinctive_sentence_valid_guard_rejects_shared_sentence():
    m = _load_harness()
    shared = "Reconciliar trilhas A2A nas duas pontas do gateway.\n"
    unique = "SSE flooding na sidebar ocorreu durante reconexão do webui.\n"
    a = f"# A\n{shared}"
    b = f"# B\n{shared}{unique}"
    df, n = _corpus_counter(m, [a, b])
    # df por frase com o MESMO splitter (invariante do guard):
    sents = m.Counter()
    for t in (a, b):
        sents.update({m.norm_phrase(s) for s in m.split_sentences(t)})
    valid = {s for s, c in sents.items() if c == 1}
    chosen = m.distinctive_sentence(b, df, n, valid=valid)
    assert "SSE flooding" in chosen and "Reconciliar" not in chosen
    # doc inteiro compartilhado não tem frase válida: vira string vazia
    assert m.distinctive_sentence(a, df, n, valid=valid) == ""


# ── unidade: derive_aliases (os 5 canais) ──────────────────────────────────

@pytest.fixture()
def corpus(tmp_path: Path):
    vault = tmp_path / "vault"
    okf = tmp_path / "okf"
    (vault / "adr").mkdir(parents=True)
    okf.mkdir(parents=True)
    return vault, okf


def _w(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_alias_channel1_fonte_licao_okf(corpus):
    m = _load_harness()
    vault, okf = corpus
    _w(okf / "licao-sse.md", "# Lição SSE\nCorpo da lição sobre flooding.")
    _w(vault / "curadoria" / "2026-09-24.md",
       "# Lição: SSE flooding\n**Fonte:** lição OKF `licao-sse.md`\nCorpo.")
    al = m.derive_aliases(vault, okf)
    assert al["curadoria/2026-09-24.md"] == ["licao-sse.md"]


def test_alias_channel2_provo_frontmatter(corpus):
    m = _load_harness()
    vault, okf = corpus
    _w(vault / "adr" / "ADR-015-foo.md", "# ADR-015 Foo\nDecisão sobre foo.")
    _w(okf / "contrato.md",
       "---\nprov:\n  wasDerivedFrom: \"adr:ADR-015\"\n---\n# Contrato\nCorpo.")
    al = m.derive_aliases(vault, okf)
    # chave = caminho relativo ao VAULT (não basename) — mesmo contrato dos
    # outros canais; o ADR é resolvido por número via nome de arquivo.
    key = "adr/ADR-015-foo.md"
    assert key in al and "contrato.md" in al[key]


def test_alias_channel3_inline_ref(corpus):
    m = _load_harness()
    vault, okf = corpus
    _w(okf / "contratos" / "politica.md", "# Política\nCorpo.")
    _w(vault / "nota.md",
       "# Nota\nver (okf/contratos/politica.md) para detalhes.")
    al = m.derive_aliases(vault, okf)
    assert al["nota.md"] == ["contratos/politica.md"]
    # regressão do `\?` literal: a forma SEM backslash também casava antes;
    # a com backslash não precisa casar, mas o canal não pode engolir junk.
    _w(vault / "ruim.md", "# Ruim\nokf/nao-existe.md")
    assert "ruim.md" not in m.derive_aliases(vault, okf)


def test_alias_channel4_title_equivalence(corpus):
    m = _load_harness()
    vault, okf = corpus
    _w(okf / "x.md", "# Recuperação Semântica HAOS\nCorpo.")
    _w(vault / "y.md",
       "---\ntitle: Recuperação Semântica HAOS\n---\n# outro título\nCorpo.")
    # frontmatter title tem precedência sobre H1
    al = m.derive_aliases(vault, okf)
    assert al["y.md"] == ["x.md"]


def test_alias_channel5_adrs_line(corpus):
    m = _load_harness()
    vault, okf = corpus
    _w(vault / "adr" / "ADR-001-a.md", "# ADR-001 A\nCorpo.")
    _w(vault / "adr" / "ADR-005-b.md", "# ADR-005 B\nCorpo.")
    _w(okf / "contrato-memoria.md",
       "# Contrato\n**ADRs:** ADR-001 (a), ADR-005 (b)\nCorpo.")
    al = m.derive_aliases(vault, okf)
    assert set(al) == {"adr/ADR-001-a.md", "adr/ADR-005-b.md"}
    assert al["adr/ADR-001-a.md"] == ["contrato-memoria.md"]


def test_alias_basename_collision_raises(corpus):
    m = _load_harness()
    vault, okf = corpus
    _w(okf / "a" / "dup.md", "# D1\nCorpo.")
    _w(okf / "b" / "dup.md", "# D2\nCorpo.")
    with pytest.raises(SystemExit):
        m.derive_aliases(vault, okf)


# ── unidade: build_gold (guards de qualidade do gold) ──────────────────────

def test_build_gold_skips_generic_title_and_ambiguous_queries(corpus):
    m = _load_harness()
    vault, okf = corpus
    uuid = "73c379bc-aaaa-bbbb-cccc-dddddddddddd"
    _w(vault / "10-Memory" / f"{uuid}.md",
       f"# Memory Note - {uuid}\n"
       "A falha do índice vetorial ocorreu na renovação do certificado.\n")
    # par byte-idêntico: nenhuma frase única, título ambíguo -> zero queries
    dup = "Reconciliar trilhas A2A nas duas pontas do gateway de produção.\n"
    _w(vault / "curadoria" / "2026-09-12.md", f"# Lição A2A\n{dup}")
    _w(vault / "curadoria" / "2026-09-13.md", f"# Lição A2A\n{dup}")
    gold = m.build_gold(vault, okf)
    targets = {g["target"] for g in gold}
    assert f"10-Memory/{uuid}.md" in targets
    # nota UUID: só content (título de template é descartado)
    assert [g["variant"] for g in gold if g["target"] == f"10-Memory/{uuid}.md"] == ["content"]
    assert uuid not in gold[0]["query"]
    # par duplicado: descartado por ambiguidade, nunca com alvo escolhido à mão
    assert "curadoria/2026-09-12.md" not in targets
    assert "curadoria/2026-09-13.md" not in targets


def test_build_gold_no_query_is_ambiguous_by_construction(corpus):
    m = _load_harness()
    vault, okf = corpus
    shared = "SSE flooding na sidebar do WebUI durante reconexão do stream.\n"
    _w(vault / "a.md", f"# A\n{shared}")
    _w(vault / "b.md", f"# B\n{shared}")
    # Título realista (≥3 chars): o guard de `exclude` é checagem de
    # SUBSTRING sobre norm_phrase da frase, então um título de 1 letra
    # ("C") engoliria toda frase candidata que contenha aquela letra.
    # No corpus real títulos têm ≥12 chars; o fixture respeita o contrato.
    _w(vault / "c.md",
       "# Alfa Beta Gama\nCada nota tem sua frase distintiva própria aqui.\n")
    gold = m.build_gold(vault, okf)
    by_q: dict = {}
    for g in gold:
        by_q.setdefault(m.norm_phrase(g["query"]), set()).add(g["target"])
    assert all(len(t) == 1 for t in by_q.values())
    assert {g["target"] for g in gold} == {"c.md"}  # a/b só tinham frase compartilhada


# ── unidade: grading (paths_of / matches) ──────────────────────────────────

def test_paths_of_known_sources_and_new_source_raises():
    m = _load_harness()
    assert m.paths_of({"source": "OKF_CANONICAL",
                       "doc": {"path": "licoes/x.md"}}) == ["licoes/x.md"]
    assert m.paths_of({"source": "RAGFLOW_HYBRID", "doc_path": "/abs/diario/1.md",
                       "chunks": [{"doc_path": "/abs/diario/2.md"}]}) == [
        "/abs/diario/1.md", "/abs/diario/2.md"]
    assert m.paths_of({"source": "NONE"}) == []
    with pytest.raises(m.UngradeableSource):
        m.paths_of({"source": "RECONCILED_MEMORY", "content": "..."})
    with pytest.raises(m.UngradeableSource):
        m.paths_of({"source": "FONTE_NOVA_DE_HOJE", "x": 1})


def test_matches_accepts_suffix_and_aliases():
    m = _load_harness()
    assert m.matches("/root/.haos/memory/diario/13-09-2026.md",
                     "diario/13-09-2026.md", [])
    assert m.matches("okf/licoes/sse.md", "curadoria/2026-09-24.md",
                     ["licoes/sse.md"])
    assert not m.matches("okf/licoes/outra.md", "curadoria/2026-09-24.md",
                         ["licoes/sse.md"])


# ── baseline versionado: agregado e sem segredos ───────────────────────────

def _walk_keys(node):
    """Todas as chaves de dict, recursivamente (lista: desce nos itens)."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_keys(v)


def test_baseline_is_aggregate_only():
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    # Contrato estrutural: o baseline é só agregados. Verifica CHAVES EXATAS
    # (não substring do blob — `n_queries_total` e a prosa de `_comment`
    # contêm "query" legitimamente).
    forbidden = {"query", "queries", "doc_path", "misses", "returned",
                 "ungradeable", "target", "aliases"}
    keys = set(_walk_keys(base))
    leaked = keys & forbidden
    assert not leaked, f"baseline contém campo proibido: {sorted(leaked)}"
    # Nenhum valor string pode carregar caminho de documento do corpus
    # (o gold é que os tem; agregado não precisa de nenhum ".md").
    def _strings(n):
        if isinstance(n, dict):
            for v in n.values():
                yield from _strings(v)
        elif isinstance(n, list):
            for v in n:
                yield from _strings(v)
        elif isinstance(n, str):
            yield n
    assert not [s for s in _strings(base) if ".md" in s], \
        "baseline contém referência a documento .md do corpus"
    assert base["hit@1"] <= base["hit@3"] <= base["gold"]["n_queries_gradable"]
    pv = base["per_variant"]
    assert sum(v["n"] for v in pv.values()) == base["gold"]["n_queries_gradable"]
    assert sum(v["h1"] for v in pv.values()) == base["hit@1"]
    assert sum(v["h3"] for v in pv.values()) == base["hit@3"]
    # Cada query GRADÁVEL tem exatamente uma fonte; as não-gradáveis ficam
    # FORA de n_queries_gradable (total = gradable + ungradeable).
    assert sum(base["sources"].values()) == base["gold"]["n_queries_gradable"]
    assert (base["gold"]["n_queries_gradable"] + base["ungradeable_count"]
            == base["gold"]["n_queries_total"])


# ── integração: bench real contra o router de produção ─────────────────────

@pytest.mark.skipif(not VAULT.exists(), reason="corpus HAOS ausente nesta máquina")
def test_bench_meets_pinned_baseline(tmp_path):
    """Gate de regressão: gold regenerado do corpus atual, run real do router,
    hit@1/hit@3 não podem cair abaixo do baseline agregado versionado."""
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    gold = tmp_path / "gold.json"
    rc = _load_harness().main(["--build-gold", "--vault", str(VAULT),
                               "--out", str(gold)])
    assert rc == 0
    rc = _load_harness().main(["--gold", str(gold), "--json",
                               "--baseline", str(BASELINE)])
    assert rc == 0, "REGRESSÃO de recuperação contra o baseline pinado"
