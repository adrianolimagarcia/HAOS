#!/usr/bin/env python3
"""staging_ttl.py — TTL do staging de memoria (arquivamento, nunca delecao).

Candidatos `pending` do staging (<home>/memory/staging/pending_candidates.json)
que nunca ganharam reforco — confidence < 0.5 — e nao sao vistos ha mais de 90
dias (last_seen_at, com fallback para created_at) saem do indice ativo e vao
para o arquivo append-only `pending_candidates.archive.jsonl` (um JSON por
linha, com `archived_at`). NADA e deletado: o registro vai inteiro para o
arquivo, e o delta journal recebe 1 linha por arquivamento com op='ttl_archive'
no formato existente {seq, op, key, record, ts}.

Por que arquivar em vez de apagar: o invariante do ciclo de vida da memoria do
HAOS e "decaimento nunca deleta" (eval memoria-decaimento). O expire da
governanca (P5) degrada status para 'expired' e mantem o registro no indice;
este TTL e o degrau seguinte — tira do indice ativo (que o load() considera
autoritativo e o dream reforca) sem perder nada: o arquivo e a trilha.

Escrita (mesmos padroes dos scripts vizinhos, cada item existe por um defeito
concreto):
  - lock flock EXCLUSIVO em arquivo DEDICADO (.staging_ttl.lock), nao no alvo:
    o inode do alvo muda a cada replace, entao flock no alvo travaria um inode
    que deixa de existir (padrao registry_append.py).
  - snapshot reescrito via tmp no MESMO diretorio + fsync no arquivo e no
    diretorio + os.replace, preservando o formato do store autoritativo
    (json.dumps indent=2, ensure_ascii=False, sort_keys=True — padrao
    MemoryStagingStore._save em hermes/platform/context/memory/staging.py).
  - arquivo e journal sao APPEND puro (modo 'a' + fsync): append-only por
    construcao; uma linha por arquivamento.
  - seq do journal = contagem de linhas + 1 (mesma semantica do store).
  - dry-run por padrao; --apply escreve. Idempotente: depois do --apply os
    registros arquivados nao estao mais no indice, entao a segunda passada
    nao encontra nada.
  - apos --apply, registra o snapshot no baseline de integridade (P5) para o
    dream nao acusar a manutencao autorizada como mudanca fora de banda.
    Melhor-esforco: sem hermes importavel, segue o fluxo normalmente.

Ordem de escrita (sobrevive a crash no meio): journal -> archive -> snapshot.
Crash antes do replace do snapshot deixa o indice intacto (o proximo run
re-arquiva; o arquivo pode ganhar linha duplicada — append-only de auditoria,
nunca perda). Crash depois do replace: tudo gravado.

Uso:
  staging_ttl.py                       # dry-run no $HAOS_HOME real
  staging_ttl.py --apply               # arquivar de verdade
  staging_ttl.py --staging-dir P       # diretorio alternativo (teste isolado)
  staging_ttl.py --ttl-days 90 --min-confidence 0.5
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import tempfile
import time
from pathlib import Path

STAGING_FILENAME = "pending_candidates.json"
DELTA_FILENAME = "pending_candidates.delta.jsonl"
ARCHIVE_FILENAME = "pending_candidates.archive.jsonl"
LOCK_NAME = ".staging_ttl.lock"
LOCK_TIMEOUT_S = 30.0

DEFAULT_TTL_DAYS = 90.0
DEFAULT_MIN_CONFIDENCE = 0.5
PENDING = "pending"


class _Lock:
    """flock exclusivo em arquivo dedicado, com timeout (padrao registry_append)."""

    def __init__(self, dir_path: Path, timeout: float = LOCK_TIMEOUT_S):
        self.path = dir_path / LOCK_NAME
        self.timeout = timeout
        self.fh = None

    def __enter__(self) -> "_Lock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+")
        self.fh = fh
        limite = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= limite:
                    raise TimeoutError(f"lock ocupado ha {self.timeout}s: {self.path}")
                time.sleep(0.05)

    def __exit__(self, *exc):
        fh = self.fh
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()


def _home() -> Path:
    raw = os.environ.get("HAOS_HOME") or os.environ.get("HERMES_HOME")
    return Path(raw) if raw else Path.home() / ".haos"


def record_age_seconds(rec: dict, now: float) -> float | None:
    """Idade pelo campo que existir: last_seen_at (preferido), criado_at/
    created_at como fallback. None se nao ha timestamp utilizavel — registro
    sem data NUNCA e arquivado (falha fechada: preserva)."""
    for field in ("last_seen_at", "created_at", "criado_at"):
        val = rec.get(field)
        if isinstance(val, (int, float)) and val > 0:
            return max(0.0, now - float(val))
    return None


def select_expired(records: dict, now: float, ttl_days: float,
                   min_confidence: float) -> list[tuple[str, dict]]:
    """Chaves dos candidatos pending, conf < limiar, mais velhos que o TTL.

    promoted ficam intocados; conf >= limiar ficam intocados; sem timestamp
    fica. Estritamente mais velho que TTL (>= seria arquivar no boundary)."""
    out = []
    for key, rec in records.items():
        if not isinstance(rec, dict):
            continue
        if rec.get("status") != PENDING:
            continue
        conf = rec.get("confidence")
        if not isinstance(conf, (int, float)) or conf >= min_confidence:
            continue
        age = record_age_seconds(rec, now)
        if age is None:
            continue
        if age > ttl_days * 86400:
            out.append((key, rec))
    return out


def journal_next_seq(delta_path: Path) -> int:
    if not delta_path.exists():
        return 1
    try:
        n = len([ln for ln in delta_path.read_text(encoding="utf-8").splitlines() if ln.strip()])
    except Exception:
        return 1
    return n + 1


def atomic_replace(path: Path, payload: str) -> None:
    """tmp no mesmo diretorio + fsync arquivo + fsync dir + os.replace
    (padrao MemoryStagingStore._save). Cria com 0600 se o alvo nao existe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        if path.exists():
            os.chmod(tmp_name, path.stat().st_mode)
        else:
            os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
        dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def append_lines(path: Path, lines: list[str]) -> None:
    """Append puro com fsync (append-only por construcao)."""
    with open(path, "a", encoding="utf-8") as fh:
        for ln in lines:
            fh.write(ln + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def register_snapshot_in_baseline(staging_dir: Path) -> str:
    """Melhor-esforco: registra o snapshot no baseline P5 (evita falso
    positivo de mudanca fora de banda no dream)."""
    home = _home()
    snap = staging_dir / STAGING_FILENAME
    try:
        rel = snap.resolve().relative_to(home.resolve())
    except (ValueError, OSError):
        return "skipped (fora da home)"
    try:
        from hermes.platform.memory.memory_governance import MemoryIntegrityChecker
    except Exception:
        return "unavailable (hermes nao importavel)"
    try:
        res = MemoryIntegrityChecker(home=home).register_files([snap])
        return f"registered={len(res.get('registered', []))}"
    except Exception as exc:
        return f"erro: {exc}"


def run(staging_dir: Path, now: float, ttl_days: float, min_confidence: float,
        apply: bool) -> dict:
    """Uma passada de TTL. Retorna relatorio (arquivadas/total_antes/...)."""
    snap = staging_dir / STAGING_FILENAME
    report = {"arquivadas": [], "total_antes": 0, "total_depois": 0, "apply": apply}
    if not snap.exists():
        return report
    try:
        records = json.loads(snap.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"ERRO: snapshot ilegivel em {snap}: {exc}")
    if not isinstance(records, dict):
        raise SystemExit(f"ERRO: snapshot nao e um mapeamento em {snap}")
    report["total_antes"] = len(records)
    doomed = select_expired(records, now, ttl_days, min_confidence)
    report["arquivadas"] = [k for k, _ in doomed]
    if not doomed or not apply:
        report["total_depois"] = len(records)
        return report

    with _Lock(staging_dir):
        # Re-le sob lock: o indice pode ter mudado desde a selecao.
        try:
            records = json.loads(snap.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SystemExit(f"ERRO: snapshot ilegivel sob lock: {exc}")
        doomed = [(k, r) for k, r in select_expired(records, now, ttl_days, min_confidence)]
        if not doomed:
            report["arquivadas"] = []
            report["total_depois"] = len(records)
            return report
        ts = time.time()
        seq = journal_next_seq(staging_dir / DELTA_FILENAME)
        journal_lines: list[str] = []
        archive_lines: list[str] = []
        for key, rec in doomed:
            archived = dict(rec)
            archived["archived_at"] = ts
            archived["archived_reason"] = "ttl_90d_low_confidence"
            journal_rec = dict(rec)
            journal_rec["status"] = "archived"
            journal_lines.append(json.dumps(
                {"seq": seq, "op": "ttl_archive", "key": key,
                 "record": journal_rec, "ts": ts},
                ensure_ascii=False, sort_keys=True))
            archive_lines.append(json.dumps(archived, ensure_ascii=False, sort_keys=True))
            seq += 1
        # Ordem: journal -> archive -> snapshot (ver docstring).
        append_lines(staging_dir / DELTA_FILENAME, journal_lines)
        append_lines(staging_dir / ARCHIVE_FILENAME, archive_lines)
        remaining = {k: v for k, v in records.items() if k not in {d[0] for d in doomed}}
        atomic_replace(snap, json.dumps(remaining, indent=2, ensure_ascii=False, sort_keys=True))
        report["arquivadas"] = [k for k, _ in doomed]
        report["total_depois"] = len(remaining)
        report["baseline"] = register_snapshot_in_baseline(staging_dir)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Arquiva candidatos pending velhos e de baixa confianca do staging (TTL).")
    ap.add_argument("--staging-dir", default=None,
                    help="diretorio do staging (default: $HAOS_HOME/memory/staging)")
    ap.add_argument("--ttl-days", type=float, default=DEFAULT_TTL_DAYS,
                    help=f"idade minima para arquivar em dias (default: {DEFAULT_TTL_DAYS})")
    ap.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE,
                    help=f"confianca abaixo da qual arquivar (default: {DEFAULT_MIN_CONFIDENCE})")
    ap.add_argument("--now", type=float, default=None,
                    help="agora em epoch s (default: relogio; para teste deterministico)")
    ap.add_argument("--apply", action="store_true",
                    help="arquivar de verdade (default: dry-run)")
    args = ap.parse_args(argv)

    staging_dir = Path(args.staging_dir) if args.staging_dir else _home() / "memory" / "staging"
    now = args.now if args.now is not None else time.time()
    try:
        rep = run(staging_dir, now, args.ttl_days, args.min_confidence, args.apply)
    except SystemExit as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    mode = "APPLY" if args.apply else "dry-run"
    print(f"[staging_ttl {mode}] dir={staging_dir} ttl={args.ttl_days}d "
          f"min_conf={args.min_confidence}")
    print(f"  candidatos no indice: {rep['total_antes']} -> {rep['total_depois']}")
    print(f"  arquivadas: {len(rep['arquivadas'])}")
    for k in rep["arquivadas"][:20]:
        print(f"    - {k}")
    if not args.apply and rep["arquivadas"]:
        print("  (dry-run: passe --apply para arquivar)")
    if rep.get("baseline"):
        print(f"  baseline: {rep['baseline']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
