"""HAOS Harness Bindings — seleção persistente de plataforma por papel do Team Graph.

Cada papel (mayor, sub_orchestrator, coder, reviewer) pode ser vinculado a um
harness de execução ("native" | "dsh" | "opencode" | "agy" | "codex" |
"claude-code" | "acp"), escolhido no painel como um seletor de modelos.

Armazenamento: ``<data_dir>/harness_bindings.json`` — um mapa simples
``{role_key: harness_name}``, gravado atomicamente. O data_dir é resolvido na
ordem: argumento explícito -> ``HAOS_DATA_DIR`` -> ``HAOS_HOME``/``HERMES_HOME``
-> ``~/.haos``. Ausência do arquivo == tudo ``native`` (default da arquitetura);
isso mantém testes e instalações novas deterministicamente em native até o
operador escolher outra plataforma.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hermes.platform.webui.harness_bindings")

# Catálogo canônico de harnesses (ordem de exibição no seletor).
HARNESS_CATALOG: tuple = ("native", "dsh", "opencode", "agy", "codex", "claude-code", "acp")

# Papéis expostos no Team Graph -> chave de binding + rótulo humano.
ROLE_KEYS: tuple = ("mayor", "sub_orchestrator", "coder", "reviewer")
ROLE_LABELS: Dict[str, str] = {
    "mayor": "\U0001F451 Town Mayor (Líder Executivo)",
    "sub_orchestrator": "\U0001F9E0 Sub-Orchestrator",
    "coder": "\u26A1 Polecat Coder (Workers)",
    "reviewer": "\U0001F441 Witness Reviewer",
}
DEFAULT_HARNESS = "native"

_detect_cache: Dict[str, tuple] = {}
_detect_lock = threading.Lock()


def detect_cached(harness_name: str, ttl_seconds: float = 20.0) -> Dict[str, Any]:
    """Probe de disponibilidade com cache curto (evita re-scan no polling do painel)."""
    name = (harness_name or DEFAULT_HARNESS).strip().lower()
    now = time.time()
    with _detect_lock:
        hit = _detect_cache.get(name)
        if hit and (now - hit[0]) < ttl_seconds:
            return hit[1]
    from hermes.platform.workers.harness_registry import HarnessRegistry

    info = HarnessRegistry.get_instance().detect(name)
    data = info.to_dict()
    with _detect_lock:
        _detect_cache[name] = (time.time(), data)
    return data


class HarnessBindingStore:
    """Persistência (JSON) do vínculo papel -> harness."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else self._default_path()
        self._lock = threading.Lock()
        self._bindings: Dict[str, str] = {}
        self._load()

    @staticmethod
    def _default_path() -> Path:
        base = (
            os.environ.get("HAOS_DATA_DIR")
            or os.environ.get("HERMES_HOME")
            or os.environ.get("HAOS_HOME")
            or str(Path.home() / ".haos")
        )
        return Path(base) / "harness_bindings.json"

    def _load(self) -> None:
        try:
            if self.path.is_file():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._bindings = {
                        str(k).strip().lower(): str(v).strip().lower()
                        for k, v in raw.items()
                        if str(k).strip().lower() in ROLE_KEYS and str(v).strip()
                    }
        except Exception as exc:  # arquivo corrompido não deve derrubar o painel
            logger.warning("harness_bindings: falha ao ler %s: %s", self.path, exc)
            self._bindings = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._bindings, indent=2, sort_keys=True, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".harness_bindings", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    # -- API pública ----------------------------------------------------- #
    def all(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._bindings)

    def get(self, role_key: str) -> str:
        key = (role_key or "").strip().lower()
        with self._lock:
            return self._bindings.get(key, DEFAULT_HARNESS)

    def set(self, role_key: str, harness_name: str) -> Dict[str, Any]:
        """Vincula papel -> harness e persiste. Retorna old/new + warning."""
        key = (role_key or "").strip().lower()
        if key not in ROLE_KEYS:
            raise ValueError(f"Papel desconhecido '{role_key}'. Válidos: {', '.join(ROLE_KEYS)}")
        name = (harness_name or DEFAULT_HARNESS).strip().lower()
        if name not in HARNESS_CATALOG:
            raise ValueError(
                f"Harness '{harness_name}' fora do catálogo. Válidos: {', '.join(HARNESS_CATALOG)}"
            )
        with self._lock:
            old = self._bindings.get(key, DEFAULT_HARNESS)
            self._bindings[key] = name
            self._save()
        warning = None
        info = detect_cached(name)
        if name != DEFAULT_HARNESS and not info.get("available"):
            warning = (
                f"'{name}' não está disponível agora ({info.get('status')}). "
                "O HAOS seguirá com fallback native até a plataforma ser instalada/liberada."
            )
        logger.info("harness binding %s: %s -> %s", key, old, name)
        return {"role": key, "old": old, "new": name, "warning": warning}

    def overview(self) -> Dict[str, Any]:
        """Estado completo para o seletor do painel (roles + catálogo + bindings)."""
        roles = [{"key": k, "label": ROLE_LABELS[k], "binding": self.get(k)} for k in ROLE_KEYS]
        catalog = [
            {"name": name, **detect_cached(name)}
            for name in HARNESS_CATALOG
        ]
        return {
            "roles": roles,
            "catalog": catalog,
            "bindings": self.all(),
            "store_path": str(self.path),
        }
