#!/usr/bin/env python3
"""Bench de recuperação do HAOS sobre o corpus REAL (vault + OKF).

Promove ao repositório o corpo de prova que existia só em ``/tmp``. O que
justifica este script existir: o gold **não é escrito à mão** e os aliases
**não são escolhidos à mão** — ambos são derivados do próprio corpus.

Duas variantes de query por documento, ambas originadas do documento (nunca
da formulação de quem avalia, o que viciaria a medição):

- ``title``   : título/H1 do documento. Mede o caminho exato (stem/título no
                gate OKF, boost de frase no FTS).
- ``content`` : frase do corpo escolhida por **raridade no corpus inteiro** e
                validada por **unicidade de frase** (df de frases == 1).
                Escolher por raridade dentro do próprio doc pegava cabeçalho
                de template (``**Scope:** project | **Confidence:** 1.00``),
                que se repete em todos os arquivos e não discrimina nada.

Aliases vault→OKF são relações DECLARADAS no corpus, por cinco canais:

1. ``**Fonte:** lição OKF \`X.md\``` nas notas ``curadoria/`` — a curadoria
   registra de qual lição OKF foi destilada;
2. frontmatter PROV-O do OKF (``prov: wasDerivedFrom: "adr:ADR-015"``);
3. referência inline a ``okf/<caminho>.md`` no corpo da nota do vault;
4. equivalência de título (título OKF == título vault, dobras NFKD+ASCII);
5. linha ``**ADRs:** ADR-001 (...), ADR-005 (...)`` no corpo de um documento
   OKF — o contrato declara de quais ADRs do vault ele deriva (N:1 legítimo).

Regra dura (anti-vício): alias é relação entre **dois documentos**, nunca
entre query e documento. Query externa jamais elege alias.

O harness é *auto-verificável*: fonte conhecida sem leitura de caminho, ou
fonte nova devolvida pelo router, faz o script **falhar** em vez de subcontar
acerto. Foi exatamente a ausência desse guard que levou um harness descartável
a ler ``doc["filepath"]`` quando ``OKFDocument.to_dict()`` expõe ``"path"`` —
e reportar aliases legítimos como misses.

Uso::

    python scripts/bench_haos_retrieval.py --build-gold
    python scripts/bench_haos_retrieval.py
    python scripts/bench_haos_retrieval.py --json --show-misses
    python scripts/bench_haos_retrieval.py --baseline benchmarks/haos_retrieval_gold.baseline.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = REPO_ROOT / "benchmarks" / "haos_retrieval_gold.json"
BASELINE_PATH = REPO_ROOT / "benchmarks" / "haos_retrieval_gold.baseline.json"

DEFAULT_VAULT = "/root/.haos/obsidian_vault"
DEFAULT_OKF = "/root/.haos/okf"

# Stopwords PT/EN mínimas. Servem para MONTAR a query a partir do corpo (e
# para o guard de cobertura), não para decidir correspondência de documento.
_STOP = {
    "de", "da", "do", "das", "dos", "a", "o", "as", "os", "em", "na", "no",
    "nas", "nos", "por", "para", "com", "sem", "e", "ou", "que", "se", "um",
    "uma", "uns", "umas", "ao", "aos", "sobre", "entre", "quando", "como",
    "mais", "menos", "muito", "ja", "foi", "ser", "era", "tem", "ter",
    "the", "of", "and", "for", "with", "that", "this", "from", "are", "was",
}

# Linhas de cabeçalho de template: repetem em todo documento gerado por
# `memory`/dream e jamais servem de query distintiva.
_TEMPLATE_LINE = re.compile(
    r"^\s*(?:#+\s*(?:Content|Metadata|Context)\b"
    # ``**Scope:**`` tem os dois-pontos DENTRO do negrito; o padrão antigo
    # exigia ``**Scope**:``, nunca casava, e as linhas de template seguiam
    # vivas como candidatas a query distintiva.
    r"|\*\*(?:Scope|Confidence|Status|Created|Updated|ID|Tags|Quando consultar):?\*\*"
    r"|\|\s*:?-{2,}"
    r")"
)


class UngradeableSource(RuntimeError):
    """O router devolveu uma fonte que o harness não sabe gradar."""


# ── normalização ────────────────────────────────────────────────────────────

def fold(s: str) -> str:
    """NFKD + dobra de acentos (mesma regra de ``OKFStore._fold``)."""
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower())
                   if not unicodedata.combining(c))


def norm_phrase(s: str) -> str:
    """Título canônico para comparação de equivalência."""
    return re.sub(r"[\s\-_/]+", " ", fold(s)).strip()


def content_tokens(s: str) -> List[str]:
    return [t for t in re.findall(r"[\w\-]+", fold(s)) if t not in _STOP]


# ── leitura de documentos ───────────────────────────────────────────────────

def split_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """Frontmatter YAML (chaves planas E aninhadas) + corpo.

    Aninhamento importa: ``wasDerivedFrom`` vive sob ``prov:`` no frontmatter
    do OKF. Ler só ``^chave:`` à esquerda perdia a relação inteira — foi o
    primeiro defeito desta derivação.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm: Dict[str, str] = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_\-]+)\s*:\s*(\S.*?)\s*$", line)
        if m:
            fm.setdefault(m.group(1), m.group(2).strip("'\""))
    return fm, text[end + 4:]


def doc_title(text: str, path: Path) -> str:
    fm, body = split_frontmatter(text)
    if fm.get("title"):
        return fm["title"]
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def body_lines(text: str) -> List[str]:
    """Corpo sem frontmatter e sem cabeçalho de template."""
    _, body = split_frontmatter(text)
    out = []
    for line in body.splitlines():
        if not line.strip() or _TEMPLATE_LINE.match(line):
            continue
        out.append(line)
    return out


def split_sentences(text: str) -> List[str]:
    """Frases candidatas do corpo (sem cabeçalhos, ≥40 chars).

    ``distinctive_sentence`` e o df de frases de ``build_gold`` usam esta
    MESMA função: se os dois splits divergissem, o guard de unicidade contaria
    frases diferentes das que são escolhidas, e a guarda viraria ruído.
    """
    lines = [l for l in body_lines(text) if not l.lstrip().startswith("#")]
    body = "\n".join(lines)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", body)
            if len(s.strip()) >= 40]


def distinctive_sentence(text: str, df: Counter, n_docs: int,
                         exclude: str = "",
                         valid: Optional[set] = None) -> str:
    """Frase do corpo de maior raridade média no corpus.

    Pontuação: IDF médio dos tokens da frase × saturação de comprimento. Sem
    o IDF de corpus, a frase escolhida era sempre o cabeçalho do template.

    ``valid`` (set de ``norm_phrase`` de frases): guard de distintividade por
    frase — só entram candidatos únicos no corpus (ver ``build_gold``). Sem
    ela, duas notas de ``diario/`` que repetem a mesma linha ``[FAIL]``
    geravam a mesma query para alvos diferentes.

    ``exclude`` (título normalizado): descarta candidatos que CONTÊM o título —
    em notas ``10-Memory`` o H1 é ``# Memory Note - <uuid>`` e o UUID tem IDF
    altíssimo; sem este filtro a variante ``content`` degenerava na variante
    ``title`` e as duas queries mediam a mesma coisa. A comparação é feita com
    ``norm_phrase`` dos DOIS lados: ``exclude`` chega com traços dobrados em
    espaço e ``fold`` preserva os traços — comparar com ``fold(s)`` nunca
    casava com um título de UUID.
    """
    idf_cache: Dict[str, float] = {}

    def idf(tok: str) -> float:
        v = idf_cache.get(tok)
        if v is None:
            v = math.log(1.0 + n_docs / max(df[tok], 1))
            idf_cache[tok] = v
        return v

    # Cabeçalhos (H1..H6) são estrutura, não conteúdo: o H1 de uma nota é o
    # próprio título (já medido pela variante ``title``) e ``## Content`` é
    # template. ``split_sentences`` os remove antes do split — sem isso o
    # corpo inteiro virava um candidato só, começando pelo H1.
    candidates = split_sentences(text)

    best, best_score = "", -1.0
    for s in candidates:
        if exclude and exclude in norm_phrase(s):
            continue
        if valid is not None and norm_phrase(s) not in valid:
            continue
        toks = content_tokens(s)
        if len(toks) < 3:
            continue
        mean_idf = sum(idf(t) for t in toks) / len(toks)
        reach = min(len(toks), 14) / 14  # favorece frases com alcance, sem premiar verbosidade
        score = mean_idf * reach
        if score > best_score:
            best, best_score = re.sub(r"\s+", " ", s).strip(), score
    return best[:300]


# ── derivação de gold e aliases ─────────────────────────────────────────────

def _md_files(root: Path) -> List[Path]:
    return sorted(p for p in root.rglob("*.md") if p.is_file())


def _okf_by_basename(okf_dir: Path) -> Dict[str, str]:
    """basename → caminho relativo. Único porque os basenames do OKF não
    colidem (verificado no corpus: 418 docs, 0 duplicados). Se um dia
    colidirem, a relação deixa de ser determinística e isso é um problema do
    corpus, não do bench — por isso o guard abaixo."""
    seen: Dict[str, str] = {}
    dup: Dict[str, int] = Counter()
    for p in _md_files(okf_dir):
        dup[p.name] += 1
        seen.setdefault(p.name, p.relative_to(okf_dir).as_posix())
    collided = sorted(k for k, v in dup.items() if v > 1)
    if collided:
        raise SystemExit(
            f"basenames OKF duplicados ({len(collided)}): {collided[:5]} — "
            "a relação declarada por basename deixou de ser determinística"
        )
    return seen


def derive_aliases(vault_dir: Path, okf_dir: Path) -> Dict[str, List[str]]:
    """Relações vault→OKF declaradas no corpus (não inferidas por quem avalia)."""
    aliases: Dict[str, List[str]] = {}
    okf_rel: Dict[str, Path] = {p.relative_to(okf_dir).as_posix(): p
                                for p in _md_files(okf_dir)}
    okf_base = _okf_by_basename(okf_dir)

    def add(vrel: str, orel: Optional[str]) -> None:
        if not orel or orel not in okf_rel:
            return
        bucket = aliases.setdefault(vrel, [])
        if orel not in bucket:
            bucket.append(orel)

    # canal 4: equivalência de título
    vault_titles: Dict[str, str] = {}
    vault_texts: Dict[str, str] = {}
    for vp in _md_files(vault_dir):
        vrel = vp.relative_to(vault_dir).as_posix()
        text = vp.read_text(encoding="utf-8", errors="replace")
        vault_texts[vrel] = text
        t = norm_phrase(doc_title(text, vp))
        if t:
            vault_titles.setdefault(t, vrel)

    okf_titles: Dict[str, str] = {}
    for orel, op in okf_rel.items():
        t = norm_phrase(doc_title(op.read_text(encoding="utf-8", errors="replace"), op))
        if t:
            okf_titles.setdefault(t, orel)

    for t, vrel in vault_titles.items():
        if t in okf_titles:
            add(vrel, okf_titles[t])

    for vrel, text in vault_texts.items():
        # canal 1: **Fonte:** lição OKF `X.md`
        for m in re.finditer(r"OKF\s+\`([^\`]+\.md)\`", text):
            add(vrel, okf_base.get(m.group(1)))
        # canal 3: referência inline okf/<caminho>.md
        # (o `\?` que havia aqui era um `?` LITERAL antes de `.md`: o canal
        # nunca disparou. Provado com 73c379bc, que cita
        # `(okf/contratos/politica-de-credenciais-do-haos.md)` no corpo.)
        for m in re.finditer(r"okf/([^\s`\)\],]+\.md)", text):
            add(vrel, m.group(1))

    # canal 2: PROV-O declarado no OKF (wasDerivedFrom sob prov:)
    adr_by_num = {m.group(1): vp.relative_to(vault_dir).as_posix()
                  for vp in _md_files(vault_dir)
                  for m in [re.search(r"ADR-(\d+)", vp.name)] if m}
    for orel, op in okf_rel.items():
        otext = op.read_text(encoding="utf-8", errors="replace")
        fm, _ = split_frontmatter(otext)
        for m in re.finditer(r"adr:ADR-(\d+)", fm.get("wasDerivedFrom", "")):
            vrel = adr_by_num.get(m.group(1))
            if vrel:
                add(vrel, orel)
        # canal 5: linha de declaração ``**ADRs:** ADR-001 (…), ADR-005 (…)`` no
        # corpo do OKF — procedência OKF→vault declarada pelo próprio documento
        # (contrato-memoria declara derivar de 6 ADRs). É relação N:1 legítima:
        # o mesmo documento serve a todos os ADRs que ele mesmo invoca.
        for line in otext.splitlines():
            m = re.match(r"^\*\*ADRs:\*\*\s*(.+)$", line.strip())
            if not m:
                continue
            for num in re.findall(r"ADR-(\d+)", m.group(1)):
                vrel = adr_by_num.get(num)
                if vrel:
                    add(vrel, orel)

    return aliases


def build_gold(vault_dir: Path, okf_dir: Path) -> List[Dict[str, Any]]:
    """Uma query por variante por documento, derivada do próprio documento."""
    aliases = derive_aliases(vault_dir, okf_dir)

    # df de corpus (vault ∪ OKF): a frase distintiva precisa ser rara no que o
    # router realmente indexa, não só dentro do vault.
    df: Counter = Counter()
    docs: List[Tuple[str, str]] = []
    for root, prefix in ((vault_dir, ""), (okf_dir, "")):
        for p in _md_files(root):
            text = p.read_text(encoding="utf-8", errors="replace")
            rel = p.relative_to(root).as_posix()
            docs.append((f"{prefix}{rel}", text))
    n_docs = len(docs)
    for _, text in docs:
        df.update(set(content_tokens("\n".join(body_lines(text)))))

    # df por FRASE (mesmo splitter de ``distinctive_sentence``): uma "frase
    # distintiva" tem de ser única no corpus. 47 frases do vault ocorrem em 2+
    # documentos (rodapé de curadoria em 19 notas, linhas ``[FAIL]`` repetidas
    # nos diários); escolhê-las como query faria a MESMA query apontar para
    # alvos diferentes — hit@1 não pode acertar os dois. Medido no baseline
    # 0.21.83: 2 content-queries nasceram de frases compartilhadas.
    sent_df: Counter = Counter()
    for _, text in docs:
        sent_df.update({norm_phrase(s) for s in split_sentences(text)})
    valid_sentences = {s for s, n in sent_df.items() if n == 1}

    gold: List[Dict[str, Any]] = []
    for vp in _md_files(vault_dir):
        vrel = vp.relative_to(vault_dir).as_posix()
        text = vp.read_text(encoding="utf-8", errors="replace")
        title = re.sub(r"\s+", " ", doc_title(text, vp)).strip()
        # Título de template ("Memory Note - <uuid>") não é query: mede o
        # acaso de um UUID no caminho, não recuperação semântica. Nesses docs
        # fica só a variante content (frase distintiva do corpo).
        generic_title = norm_phrase(title).startswith("memory note ")
        body_q = distinctive_sentence(text, df, n_docs,
                                      exclude=norm_phrase(title),
                                      valid=valid_sentences)
        entry_alias = aliases.get(vrel, [])
        variants = [] if generic_title else [("title", title)]
        variants.append(("content", body_q))
        for variant, q in variants:
            if len(q or "") < 12:
                continue
            gold.append({"query": q, "target": vrel, "variant": variant,
                         "aliases": entry_alias})

    # Ambiguidade estrutural: notas ``curadoria/`` carregam a mesma frase de
    # um dia para o outro (carryover). Uma query que pertence a 2+ alvos não é
    # distintiva — hit@1 não pode acertar os dois. Descartar a query, não
    # escolher alvo: escolher seria o avaliador viciando o gold.
    by_q: Dict[str, set] = {}
    for e in gold:
        by_q.setdefault(norm_phrase(e["query"]), set()).add(e["target"])
    gold = [e for e in gold if len(by_q[norm_phrase(e["query"])]) == 1]
    return gold


# ── avaliação ───────────────────────────────────────────────────────────────

def paths_of(res: Dict[str, Any]) -> List[str]:
    """Caminhos de documento por fonte. FONTES CONHECIDAS SÃO EXAUSTIVAS.

    Fonte nova sem leitura definida = erro nosso, não do router: falhar em
    vez de subcontar acerto.
    """
    if not res:
        return []
    src = res.get("source", "")
    out: List[str] = []
    if src in ("OKF_CANONICAL", "OKF_BROAD", "OKF_BROAD_MATCH"):
        doc = res.get("doc")
        if isinstance(doc, dict):
            # OKFDocument.to_dict() expõe "path" (relative_path). "filepath"
            # só existe no objeto — nunca no dict.
            p = doc.get("path") or doc.get("filepath") or ""
            if p:
                out.append(str(p))
    elif src == "RAGFLOW_HYBRID":
        dp = res.get("doc_path")
        if dp:
            out.append(dp)
        for c in res.get("chunks", []) or []:
            p = c.get("doc_path")
            if p and p not in out:
                out.append(p)
    elif src == "RAPTOR_TREE":
        for key in ("doc_path", "source_path", "path"):
            v = res.get(key)
            if v:
                out.append(v)
    elif src in ("GRAPHRAG", "RAG_PROBABILISTIC"):
        v = res.get("doc_path") or res.get("path")
        if v:
            out.append(v)
    elif src == "RECONCILED_MEMORY":
        raise UngradeableSource(
            "RECONCILED_MEMORY não expõe caminho de documento — impossível "
            f"gradar. payload keys={sorted(res.keys())}"
        )
    elif src in ("NONE", ""):
        return []
    else:
        raise UngradeableSource(f"fonte desconhecida no harness: {src!r}")
    return out


def matches(p: str, target: str, aliases: Iterable[str]) -> bool:
    if not p:
        return False
    if p.endswith(target):
        return True
    return any(p.endswith(a) for a in aliases)


def evaluate(router, gold: List[Dict[str, Any]]) -> Dict[str, Any]:
    import time
    per_variant: Dict[str, Dict[str, int]] = {}
    sources: Dict[str, int] = {}
    misses: List[Dict[str, Any]] = []
    ungradeable: List[Dict[str, Any]] = []
    t0 = time.time()

    for item in gold:
        q, tgt, variant = item["query"], item["target"], item["variant"]
        alias = item.get("aliases", [])
        try:
            res = router.query(query_str=q, mode="hybrid")
            pl = paths_of(res)
        except UngradeableSource as exc:
            ungradeable.append({"query": q, "target": tgt, "reason": str(exc)})
            continue
        src = res.get("source", "NONE") if res else "NONE"
        sources[src] = sources.get(src, 0) + 1
        h1 = bool(pl) and matches(pl[0], tgt, alias)
        h3 = any(matches(p, tgt, alias) for p in pl[:3])
        rank = next((i + 1 for i, p in enumerate(pl) if matches(p, tgt, alias)), None)
        st = per_variant.setdefault(variant, {"n": 0, "h1": 0, "h3": 0})
        st["n"] += 1
        st["h1"] += int(h1)
        st["h3"] += int(h3)
        if not h1:
            misses.append({
                "query": q, "target": tgt, "variant": variant, "source": src,
                "rank": rank, "returned": [p.split("/")[-1] for p in pl[:3]],
            })

    total = sum(v["n"] for v in per_variant.values())
    h1 = sum(v["h1"] for v in per_variant.values())
    h3 = sum(v["h3"] for v in per_variant.values())
    elapsed = time.time() - t0
    return {
        "n_queries": total,
        "hit@1": h1,
        "hit@3": h3,
        "hit@1_rate": round(h1 / total, 4) if total else 0.0,
        "hit@3_rate": round(h3 / total, 4) if total else 0.0,
        "per_variant": per_variant,
        "sources": sources,
        "ungradeable_count": len(ungradeable),
        "ungradeable": ungradeable,
        "misses": misses,
        "elapsed_s": round(elapsed, 2),
        "ms_per_query": round(elapsed * 1000 / total, 1) if total else 0.0,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", default=DEFAULT_VAULT)
    ap.add_argument("--okf", default=DEFAULT_OKF)
    ap.add_argument("--build-gold", action="store_true",
                    help="deriva o gold do corpus e escreve em --out")
    ap.add_argument("--out", default=str(GOLD_PATH))
    ap.add_argument("--gold", default=str(GOLD_PATH))
    ap.add_argument("--limit", type=int, default=0, help="avalia só as N primeiras queries")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show-misses", action="store_true")
    ap.add_argument("--baseline", help="falha (exit 1) se regredir vs este JSON")
    args = ap.parse_args(argv)

    vault, okf = Path(args.vault), Path(args.okf)
    if not vault.exists():
        print(f"vault inexistente: {vault}", file=sys.stderr)
        return 2

    if args.build_gold:
        gold = build_gold(vault, okf)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(gold, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        n_docs = len({g["target"] for g in gold})
        n_alias = len({g["target"] for g in gold if g["aliases"]})
        print(f"gold: {len(gold)} queries sobre {n_docs} documentos "
              f"({n_alias} docs com alias declarado) -> {out}")
        return 0

    if not okf.exists():
        print(f"aviso: OKF ausente ({okf}); aliases não verificáveis", file=sys.stderr)

    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    if args.limit:
        gold = gold[:args.limit]

    sys.path.insert(0, str(REPO_ROOT))
    # O router resolve o índice por get_hermes_home(). Sem binding, o bench
    # graduou em silêncio um índice DIFERENTE do corpus medido (observado:
    # hit@1=0/204 contra o .haos do workspace). Binding determinístico: o
    # home é o pai do vault — grada o índice cujo home é o corpus testado.
    os.environ["HERMES_HOME"] = str(vault.parent)
    import tools.haos_memory_tools as hmt
    router = hmt._get_hybrid_router()

    rep = evaluate(router, gold)

    if args.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        print(f"queries={rep['n_queries']}  hit@1={rep['hit@1']} "
              f"({rep['hit@1_rate']:.3f})  hit@3={rep['hit@3']} ({rep['hit@3_rate']:.3f})")
        for v, st in sorted(rep["per_variant"].items()):
            print(f"  [{v:7}] hit@1={st['h1']}/{st['n']} ({st['h1']/st['n']:.3f})  "
                  f"hit@3={st['h3']}/{st['n']} ({st['h3']/st['n']:.3f})")
        print(f"fontes={rep['sources']}  não-gradáveis={rep['ungradeable_count']}  "
              f"tempo={rep['elapsed_s']}s ({rep['ms_per_query']} ms/query)")
        for u in rep["ungradeable"][:3]:
            print(f"    ⚠ {u['target']} :: {u['reason'][:110]}")
        if args.show_misses:
            print(f"\nmisses @1: {len(rep['misses'])}")
            for m in rep["misses"]:
                print(f"  [{m['source']}] rank={m['rank']} {m['target']}")
                print(f"     Q: {m['query'][:90]}")
                print(f"     -> {m['returned']}")

    if args.baseline:
        base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        ok = True
        for key in ("hit@1", "hit@3"):
            if rep[key] < base.get(key, 0):
                print(f"REGRESSÃO: {key} {rep[key]} < baseline {base[key]}", file=sys.stderr)
                ok = False
        if not ok:
            return 1
        print("gate de regressão: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
