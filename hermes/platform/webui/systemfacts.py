"""HAOS Standalone — fatos reais do sistema para o painel (Sistema/Config).

Vasculha o ambiente e devolve ONDE cada coisa está guardada e COMO o sistema
de agentes está configurado — sem inventar nada: ausência de dado vira campo
vazio honesto. Detecta múltiplas ``HERMES_HOME`` (ex.: home do agente da
máquina e home do operador/dev), pois vault Obsidian, GraphRAG e kanban podem
viver em homes diferentes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_DEV_HOME_CANDIDATES = (
    "/run/media/adriano/e681b5ac-a4fb-44d4-aebf-9d6584065787/dsh-projetos/.haos",
    "/root/.haos",
)


def _default_home() -> Path:
    import os
    env = os.environ.get("HAOS_HOME") or os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser()
    ws_haos = Path("/run/media/adriano/e681b5ac-a4fb-44d4-aebf-9d6584065787/dsh-projetos/.haos")
    if ws_haos.exists():
        return ws_haos
    if (Path.home() / ".haos").exists():
        return Path.home() / ".haos"
    return Path.home() / ".haos"


def _discover_homes() -> List[Path]:
    """Homes com sinal real de uso (config/vault/graphrag/kanban/state)."""
    import os
    found: List[Path] = []
    seen = set()

    def _add(p: Path) -> None:
        p = p.resolve()
        if str(p) in seen:
            return
        markers = [p / "config.yaml", p / "kanban.db", p / "state.db",
                   p / "vault", p / "graphrag", p / "memories", p / "haos"]
        if any(m.exists() for m in markers):
            seen.add(str(p))
            found.append(p)

    _add(_default_home())
    home_dir = Path.home() / ".haos"
    if home_dir != _default_home():
        _add(home_dir)
    for cand in _DEV_HOME_CANDIDATES:
        _add(Path(cand))
    extra = os.environ.get("HAOS_HOMES") or os.environ.get("HERMES_HOMES")
    if extra:
        for part in extra.split(os.pathsep):
            if part:
                _add(Path(part))
    return found


_SECRET_KEYS = ("api_key", "apikey", "token", "secret", "password", "cookie", "client_secret")


def _mask(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("********" if any(s in str(k).lower() for s in _SECRET_KEYS)
                    else _mask(v)) for k, v in value.items()}
    return value


def _config_summary(home: Path) -> Dict[str, Any]:
    cfg = home / "config.yaml"
    out: Dict[str, Any] = {"config_path": str(cfg), "exists": cfg.is_file()}
    if not cfg.is_file():
        return out
    try:
        import yaml  # type: ignore[import-not-found]
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw = _mask(raw)  # segredos nunca saem no payload
    out["model"] = raw.get("model")
    out["delegation"] = raw.get("delegation")
    out["agent"] = raw.get("agent")
    out["memory"] = raw.get("memory")
    out["terminal"] = raw.get("terminal")
    out["display"] = raw.get("display")
    return out


def _knowledge(home: Path) -> Dict[str, Any]:
    vault = home / "vault"
    gr = home / "graphrag"
    notes = 0
    if vault.is_dir():
        try:
            notes = len(list(vault.rglob("*.md")))
        except Exception:  # noqa: BLE001
            notes = 0
    entities = 0
    rel = 0
    if (gr / "entities.csv").is_file():
        try:
            entities = max(0, sum(1 for _ in (gr / "entities.csv").open(encoding="utf-8")) - 1)
        except Exception:  # noqa: BLE001
            entities = 0
    if (gr / "relationships.csv").is_file():
        try:
            rel = max(0, sum(1 for _ in (gr / "relationships.csv").open(encoding="utf-8")) - 1)
        except Exception:  # noqa: BLE001
            rel = 0
    memories = home / "memories"
    mem_files = sorted(p.name for p in memories.iterdir()) if memories.is_dir() else []
    return {
        "vault": {"path": str(vault), "notes": notes} if vault.is_dir() else None,
        "graphrag": {"path": str(gr), "entities": entities,
                     "relationships": rel} if gr.is_dir() else None,
        "memories": {"path": str(memories), "files": mem_files} if memories.is_dir() else None,
    }


def _engine(data_dir: Path) -> Dict[str, Any]:
    return {
        "data_dir": str(data_dir),
        "kanban_db": str(data_dir / "kanban.db"),
        "events_db": str(data_dir / "events.db"),
        "settings_file": str(data_dir / "settings.json"),
        "config_backups": str(data_dir / "config-backups"),
    }


_MODEL_VALUE_KEYS = {"default", "leaf", "orchestrator", "aux", "model", "fallback", "id"}
_LOOKS_LIKE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\\-]{1,80}$")


def _models_suggestions() -> List[str]:
    """Sugestões = modelos REALMENTE em uso nas configs detectadas (sem chutes).

    Coleta só valores sob chaves tipo-modelo (default/orchestrator/leaf/…), já
    sobre a árvore mascarada — segredo e lixo de config nunca viram sugestão.
    O catálogo completo do provider custom (gateway OpenAI-compatível) só existe
    em runtime; aqui nunca se inventa id. Campo livre aceita qualquer id.
    """
    out: List[str] = []
    seen = set()

    def _grab(section: Any) -> None:
        if isinstance(section, dict):
            for k, v in section.items():
                if isinstance(v, str):
                    if k in _MODEL_VALUE_KEYS and re.fullmatch(_LOOKS_LIKE_MODEL, v) \
                            and not v.lower().startswith("sk-") and v != "********":
                        if v not in seen:
                            seen.add(v)
                            out.append(v)
                else:
                    _grab(v)
        elif isinstance(section, list):
            for x in section:
                _grab(x)

    for home in _discover_homes():
        cfg = home / "config.yaml"
        if not cfg.is_file():
            continue
        try:
            import yaml  # type: ignore[import-not-found]
            raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        raw = _mask(raw)  # chaves secretas viram "********" antes da coleta
        for key in ("model", "delegation", "agent", "providers"):
            if isinstance(raw, dict) and key in raw:
                _grab(raw[key])
    return out[:20]


def gather(data_dir: Path) -> Dict[str, Any]:
    homes = []
    for home in _discover_homes():
        homes.append({
            "home": str(home),
            "role": "HAOS Principal" if ".haos" in str(home) else "Ambiente Detectado",
            **{"config": _config_summary(home)},
            **{"knowledge": _knowledge(home)},
        })
    return {
        "homes": homes,
        "engine": _engine(Path(data_dir)),
        "models_suggestions": _models_suggestions(),
        "note": "Paths e modelos lidos do sistema real; api_key nunca exposta.",
    }
