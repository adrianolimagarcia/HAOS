#!/usr/bin/env python3
"""okf_mark_legacy.py — marca o debito de contrato das licoes OKF legadas.

Contexto (dono, 29/09/2026): as licoes destiladas antes do contrato OKF v0.2
(contrato-padrao-universal-licoes-okf.md: 4 secoes canonicas + bloco prov) nao
serao reescritas em massa. Para o linter nao contar isso como debito
permanente, cada licao recebe uma chave `contract:` no frontmatter:

  - contract: v0.2 -> licao ja tem as 4 secoes canonicas (conforme)
  - contract: v0.1 -> licao legada fora do contrato (debito aceito, marcado)
  - ja-marcada     -> frontmatter ja tem a chave `contract:` (nao tocar)

Regras de escrita (cada uma existe por um defeito concreto):
  - dry-run por padrao; --apply escreve. Idempotente: a segunda passada nao
    encontra pendencia (a chave inserida e o que a classificacao reconhece).
  - Insercao PURAMENTE TEXTUAL: nunca re-serializa o YAML. 3 licoes tem YAML
    malformado pre-existente (escapes invalidos no `title:`); re-serializar
    corromperia esses arquivos e reformataria todos os outros. O corpo fica
    byte-identico: a unica mudanca e a linha `contract:` inserida, e o script
    VERIFICA isso (remove a linha inserida e compara com o original) antes de
    gravar qualquer arquivo.
  - Frontmatter sem bloco prov ou malformado: a chave e inserida no fim do
    bloco, antes do delimitador de fechamento, sem tocar no resto.
  - Arquivo sem frontmatter (ou com bloco nao fechado): insere bloco novo no
    topo (`---\\ncontract: vX\\n---`).
  - Escrita atomica: tmp no MESMO diretorio + fsync no arquivo e no diretorio
    + os.replace, preservando o modo do original (padrao dos scripts vizinhos).
  - Lock flock em arquivo DEDICADO (nao no alvo: o inode muda a cada replace).
  - Marcadores de fila (.done/.skip) nao sao licoes: o glob `*.md` ja os
    exclui; ha guarda extra por seguranca.
  - Apos --apply, registra os arquivos alterados no baseline de integridade
    (memory/integrity/baseline.json via MemoryIntegrityChecker.register_files)
    para o dream nao acusar a manutencao autorizada como mudanca fora de banda.
    Melhor-esforco: sem hermes importavel, segue o fluxo normalmente.

Uso:
  okf_mark_legacy.py                  # dry-run: relatorio por classe
  okf_mark_legacy.py --apply          # escreve de verdade
  okf_mark_legacy.py --licoes-dir P   # diretorio alternativo (teste isolado)
"""
from __future__ import annotations

import argparse
import fcntl
import os
import re
import sys
import tempfile
import time
from pathlib import Path

LOCK_NAME = ".okf_mark_legacy.lock"
LOCK_TIMEOUT_S = 30.0

# As 4 secoes canonicas do contrato (o template permite sufixo entre
# parenteses; os pontos cobrem variacoes de acentuacao).
SECTION_PATTERNS = (
    re.compile(r"^##\s*1\.\s*O Princ.pio Abstrato Universal", re.M),
    re.compile(r"^##\s*2\.\s*A Heur.stica de Diagn.stico", re.M),
    re.compile(r"^##\s*3\.\s*Matriz de Cen.rios", re.M),
    re.compile(r"^##\s*4\.\s*Estudo de Caso", re.M),
)
CONTRACT_KEY_RE = re.compile(r"^contract\s*:", re.M)
FM_CLOSE_TOKENS = ("---", "...")


def has_all_sections(text: str) -> bool:
    return all(p.search(text) is not None for p in SECTION_PATTERNS)


def frontmatter_close(lines: list[str]) -> int | None:
    """Indice da linha que fecha o frontmatter, ou None se nao ha bloco."""
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() in FM_CLOSE_TOKENS:
            return i
    return None


def classify(text: str) -> tuple[str, str | None]:
    """(classe, contract_a_inserir ou None se nao ha o que inserir).

    Classe: ja_marcada (tem a chave) | v0.2 (conforme) | v0.1 (legada).
    A chave so conta se estiver DENTRO do bloco de frontmatter: `contract:`
    no corpo nao e marcador, e uma licao sem frontmatter nunca esta marcada.
    """
    lines = text.split("\n")
    close = frontmatter_close(lines)
    if close is not None:
        block = "\n".join(lines[1:close])
        if CONTRACT_KEY_RE.search(block):
            return "ja_marcada", None
    contract = "v0.2" if has_all_sections(text) else "v0.1"
    return contract, contract


def mark(text: str, contract: str) -> str:
    """Novo texto com a linha `contract:` inserida.

    Inserida no fim do bloco de frontmatter (antes do fechamento) ou num bloco
    novo no topo quando nao ha frontmatter usavel. O corpo e preservado
    byte a byte — a verificacao e por construcao: no caso com frontmatter,
    remover a linha inserida devolve o original; no caso sem frontmatter, o
    original e sufixo exato do novo texto.
    """
    lines = text.split("\n")
    close = frontmatter_close(lines)
    entry = f"contract: {contract}"
    if close is None:
        new_text = f"---\n{entry}\n---\n\n" + text
        assert new_text.endswith(text), "insercao alterou o corpo — abortando"
        return new_text
    new_lines = lines[:close] + [entry] + lines[close:]
    new_text = "\n".join(new_lines)
    check = new_text.split("\n")
    del check[close]
    assert "\n".join(check) == text, "insercao alterou o corpo — abortando"
    return new_text


def atomic_write(path: Path, content: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        st = path.stat()
        os.chmod(tmp_name, st.st_mode)
        os.replace(tmp_name, path)
        dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


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


def _under(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def register_in_baseline(paths: list[Path]) -> str:
    """Melhor-esforso: registra as escritas autorizadas no baseline P5."""
    home = _home()
    inside = [p for p in paths if _under(p, home)]
    if not inside:
        return "skipped (fora da home)"
    try:
        from hermes.platform.memory.memory_governance import MemoryIntegrityChecker
    except Exception:
        return "unavailable (hermes nao importavel)"
    try:
        res = MemoryIntegrityChecker(home=home).register_files(inside)
        return f"registered={len(res.get('registered', []))}"
    except Exception as exc:
        return f"erro: {exc}"


def scan(licoes_dir: Path) -> dict:
    """Classifica todas as licoes. Retorna contagens + lista de pendentes."""
    counts = {"v0.1": 0, "v0.2": 0, "ja_marcada": 0, "erro": 0, "total": 0}
    pending: list[tuple[Path, str]] = []
    errors: list[str] = []
    for f in sorted(licoes_dir.glob("*.md")):
        if f.name.endswith((".done", ".skip")):  # marcadores de fila (defesa)
            continue
        counts["total"] += 1
        try:
            text = f.read_text(encoding="utf-8")
            klass, contract = classify(text)
        except Exception as exc:
            counts["erro"] += 1
            errors.append(f"{f.name}: {exc}")
            continue
        counts[klass] += 1
        if contract is not None:
            pending.append((f, contract))
    return {"counts": counts, "pending": pending, "errors": errors}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Marca o debito de contrato (contract: v0.1/v0.2) nas licoes OKF.")
    ap.add_argument("--licoes-dir", default=None,
                    help="diretorio de licoes (default: $HAOS_HOME/okf/licoes)")
    ap.add_argument("--apply", action="store_true",
                    help="escreve de verdade (default: dry-run)")
    args = ap.parse_args(argv)

    licoes = Path(args.licoes_dir) if args.licoes_dir else _home() / "okf" / "licoes"
    if not licoes.is_dir():
        print(f"ERRO: diretorio de licoes ausente: {licoes}", file=sys.stderr)
        return 2

    result = scan(licoes)
    c = result["counts"]
    mode = "APPLY" if args.apply else "dry-run"
    print(f"[okf_mark_legacy {mode}] dir={licoes}")
    print(f"  total={c['total']}  v0.1={c['v0.1']}  v0.2={c['v0.2']}  "
          f"ja_marcada={c['ja_marcada']}  erro={c['erro']}")
    for e in result["errors"][:10]:
        print(f"  ERRO: {e}")
    pend = result["pending"]
    if not pend:
        print("  0 pendencia(s) — nada a escrever (idempotente).")
        return 0
    print(f"  pendentes a marcar: {len(pend)} "
          f"(v0.1={sum(1 for _, k in pend if k == 'v0.1')}, "
          f"v0.2={sum(1 for _, k in pend if k == 'v0.2')})")
    if not args.apply:
        print("  (dry-run: passe --apply para escrever)")
        return 0

    changed: list[Path] = []
    with _Lock(licoes):
        for path, contract in pend:
            try:
                text = path.read_text(encoding="utf-8")
                # Re-classifica sob lock: outro escritor pode ter marcado antes.
                klass, fresh = classify(text)
                if fresh is None:
                    continue
                new_text = mark(text, fresh)
                atomic_write(path, new_text)
                changed.append(path)
            except Exception as exc:
                print(f"  ERRO ao marcar {path.name}: {exc}", file=sys.stderr)
                return 1
    print(f"  aplicados: {len(changed)} arquivo(s) marcados.")
    print(f"  baseline: {register_in_baseline(changed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
