"""B3 — Obsidian real (memória canônica/ADR via vault, forma B/mock).

Camada B com contrato fechado: o VAULT é a fonte (diretório de notas .md com
convenção ADR em ``adrs/``), lido com stdlib (filesystem = transporte). A CLI
(ex.: ``rg``) é SEAM opcional p/ busca: sem CLI configurada o adapter usa o
matcher determinístico embutido; com CLI, delega em subprocesso peer
(``_cli + [query, vault]``). Fail-closed: vault ausente => available() False e
qualquer leitura levanta ``ObsidianError`` — nunca devolve dados de outro
vault nem dados fabricados.
"""

import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional


class ObsidianError(RuntimeError):
    pass


class ObsidianAdapter:
    """Adapter da base canônica Obsidian (notas + ADRs por projeto)."""

    def __init__(self, vault_path: str, *, cli: Optional[List[str]] = None,
                 adr_prefix: str = "adrs"):
        self.vault_path = vault_path
        self.adr_prefix = adr_prefix
        self.cli = cli  # e.g. ["rg", "-l"] — subprocesso quando presente

    # ------------------------------------------------------------------ #
    # probe / contrato
    # ------------------------------------------------------------------ #
    def available(self) -> bool:
        """Vault legível (diretório com pelo menos um .md)."""
        if not os.path.isdir(self.vault_path):
            return False
        return any(
            p.suffix == ".md"
            for p in _walk(self.vault_path)
        )

    def _require(self) -> None:
        if not self.available():
            raise ObsidianError(
                f"obsidian vault not available at {self.vault_path!r} "
                f"(fail-closed)")

    # ------------------------------------------------------------------ #
    # leitura
    # ------------------------------------------------------------------ #
    def list_notes(self) -> List[str]:
        """Notas relativas ao vault (caminhos POSIX, ordenadas)."""
        self._require()
        return sorted(
            str(p.relative_to(self.vault_path)).replace(os.sep, "/")
            for p in _walk(self.vault_path)
            if p.suffix == ".md"
        )

    def read_note(self, relative_path: str) -> Dict[str, Any]:
        self._require()
        full = os.path.normpath(os.path.join(self.vault_path, relative_path))
        if not full.startswith(os.path.normpath(self.vault_path) + os.sep) \
                and os.path.normpath(full) != os.path.normpath(self.vault_path):
            raise ObsidianError("path escapes the vault (fail-closed)")
        if not os.path.isfile(full) or not full.endswith(".md"):
            raise ObsidianError(f"note not found: {relative_path!r}")
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        return {"path": relative_path.replace(os.sep, "/"),
                "chars": len(content),
                "lines": content.count("\n") + 1,
                "content": content}

    def get_adr(self, adr_id: str) -> Optional[Dict[str, Any]]:
        """ADR por id: acha ``ADR-<id>`` no nome do arquivo (ex.: ADR-001-auth ou 001)."""
        self._require()
        target = adr_id.upper()
        if target.startswith("ADR-"):
            target = target[4:]
        prefix_dir = os.path.join(self.vault_path, self.adr_prefix)
        for p in _walk(prefix_dir if os.path.isdir(prefix_dir) else self.vault_path):
            if p.suffix != ".md":
                continue
            parts = p.stem.split("-", 2)  # ['ADR', '<id>', '<slug>...']
            if len(parts) >= 2 and parts[0].upper() == "ADR" \
                    and parts[1].upper() == target:
                return self.read_note(str(p.relative_to(self.vault_path)))
        return None

    def search(self, query: str) -> List[str]:
        """Notas que casam; CLI seam quando presente, senão matcher stdlib."""
        self._require()
        if self.cli is not None and shutil.which(self.cli[0]):
            proc = subprocess.run(
                list(self.cli) + [query, self.vault_path],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode not in (0, 1):  # rg 1 = sem matches
                raise ObsidianError(
                    f"obsidian search CLI failed rc={proc.returncode}")
            hits = sorted({ln.strip() for ln in proc.stdout.splitlines() if ln.strip()})
            return [os.path.relpath(h, self.vault_path).replace(os.sep, "/")
                    for h in hits]
        lowered = query.lower()
        return [
            str(p.relative_to(self.vault_path)).replace(os.sep, "/")
            for p in _walk(self.vault_path)
            if p.suffix == ".md"
            and lowered in p.read_text(encoding="utf-8", errors="replace").lower()
        ]


def _walk(directory: str):
    import pathlib
    root = pathlib.Path(directory)
    if not root.is_dir():
        return []
    return list(root.rglob("*.md"))
