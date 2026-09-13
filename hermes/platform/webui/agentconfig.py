"""HAOS Standalone WebUI — Configuração completa do AGENTE (config.yaml real).

A v1 (hermes-webui) expunha praticamente toda a configuração do Hermes. Este
módulo traz isso para a UI standalone operando sobre o MESMO ``config.yaml``
que o agente usa (``HERMES_HOME/config.yaml``), sem duplicar estado:

* **Leitura:** ``hermes_cli.config.read_raw_config()`` — o que está de fato
  persistido (valores exatamente como escritos, refs ``${VAR}`` preservadas,
  nada de segredos expandidos vazando para a UI).
* **Escrita:** patch parcial em caminhos pontilhados (``display.skin``) via
  ``hermes_cli.config.save_config(partial, merge_existing=True)`` — deep-merge
  sobre o raw on-disk, então seções não tocadas nunca são derrubadas, defaults
  não são contaminados e refs de env template são preservadas.
* **Backup:** cópia datada do ``config.yaml`` em ``<data_dir>/config-backups/``
  antes de cada gravação (restauração manual honesta).
* **Fail-closed:** sem ``config.yaml`` → GET devolve estado vazio com o caminho;
  POST responde 409 ``config_missing``. Perfil gerenciado (``is_managed()``) →
  leitura permite, escrita bloqueada com 409 ``managed``.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_VALID_SECTION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VALID_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _config_seam() -> Any:
    """Import canônico (hermes_cli) — lazy, só quando o endpoint é usado."""
    from hermes_cli import config as cfg  # noqa: PLC0415
    return cfg


def _scalar_kind(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if value is None:
        return "null"
    if isinstance(value, str):
        return "str"
    return "json"


def _is_list_of_scalars(value: Any) -> bool:
    return isinstance(value, list) and all(
        not isinstance(v, (dict, list)) for v in value)


def _is_secret_like(path: str, value: Any) -> bool:
    """Nunca exibimos valores que parecem credenciais (api_key real/token).

    Caminhos com api_key/token/secret/password/cookie + string longa sem
    espaços são tratados como segredo (mascarados na UI), mesmo que o formato
    seja só alfanumérico (ex.: sk-...). O valor real permanece apenas no
    config.yaml.
    """
    secret_hint = any(tok in path.lower() for tok in
                      ("api_key", "apikey", "token", "secret", "password", "cookie"))
    if not secret_hint:
        return False
    if not isinstance(value, str) or len(value) < 16:
        return False
    return not any(ch.isspace() for ch in value)


def _leaf_fields(node: Dict[str, Any], prefix: str = "") -> List[Dict[str, Any]]:
    """Campos editáveis: folhas escalares/listas curtas e dicts aninhados até
    profundidade 2 viram path pontilhado; nós complexos ficam como 'json'."""
    fields: List[Dict[str, Any]] = []

    def walk(value: Any, path: str, depth: int) -> None:
        if isinstance(value, dict):
            if depth >= 2 and all(
                    not isinstance(v, (dict, list)) or _is_list_of_scalars(v)
                    for v in value.values()):
                # dict plano de escalares em profundidade 2+ -> JSON editável
                fields.append({"path": path, "kind": "json",
                               "value": _safe_repr(value)})
                return
            for key in sorted(value.keys()):
                if not _VALID_KEY_RE.match(str(key)):
                    continue
                walk(value[key], f"{path}.{key}" if path else str(key), depth + 1)
            return
        if isinstance(value, list):
            if _is_list_of_scalars(value):
                fields.append({"path": path, "kind": "list",
                               "value": list(value)})
            else:
                fields.append({"path": path, "kind": "json",
                               "value": _safe_repr(value)})
            return
        kind = _scalar_kind(value)
        display = value
        if _is_secret_like(path, value):
            kind, display = "secret", "********"
        fields.append({"path": path, "kind": kind, "value": display})

    for key in sorted(node.keys()):
        if not _VALID_KEY_RE.match(str(key)):
            continue
        walk(node[key], f"{prefix}.{key}" if prefix else str(key), 1)
    return fields


def _safe_repr(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _deep_get(node: Dict[str, Any], path: str) -> Optional[Any]:
    cur: Any = node
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _parse_leaf_value(path: str, kind: str, raw: Any) -> Any:
    """Converte o valor vindo da UI de volta ao tipo canônico da config."""
    if kind in ("bool", "int", "float", "null", "str", "secret"):
        return raw  # já tipado pela UI (checkbox/number/text)
    if kind == "list":
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            return parts
        return raw
    if kind == "json":
        if isinstance(raw, (dict, list)):
            return raw
        try:
            parsed = json.loads(str(raw))
        except ValueError as exc:
            raise ValueError(f"{path}: JSON inválido: {exc}") from exc
        return parsed
    return raw


def describe_config() -> Dict[str, Any]:
    """Snapshot do config.yaml real em seções editáveis (leitura pura)."""
    cfg = _config_seam()
    path = cfg.get_config_path()
    exists = path.is_file()
    out: Dict[str, Any] = {
        "config_path": str(path),
        "exists": exists,
        "managed": cfg.is_managed() if exists else False,
        "sections": {},
    }
    if not exists:
        return out
    try:
        raw = cfg.read_raw_config()
    except Exception as exc:  # noqa: BLE001 - fail-closed: nunca 500 na UI
        out["error"] = f"config.yaml ilegível: {exc}"
        return out
    if not isinstance(raw, dict):
        out["error"] = "config.yaml precisa ser um mapeamento YAML no topo."
        return out
    sections: Dict[str, Any] = {}
    for section, node in sorted(raw.items()):
        if isinstance(node, dict) and _VALID_SECTION_RE.match(str(section)):
            sections[str(section)] = {
                "title": str(section),
                "fields": _leaf_fields(node, prefix=str(section)),
            }
        elif _VALID_SECTION_RE.match(str(section)):
            sections[str(section)] = {
                "title": str(section),
                "fields": [{"path": str(section), "kind": _scalar_kind(node),
                            "value": "********" if _is_secret_like(str(section), node) else node}],
            }
    out["sections"] = sections
    return out


def patch_config(updates: Dict[str, Any], backup_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Aplica patch parcial de paths pontilhados ao config.yaml real.

    ``updates``: {"display.skin": "midnight", "display.compact_ui": true}.
    Valores devem já estar tipados (bool/int/float/str/list) ou JSON string
    para campos kind='json'. Nunca remove seções não mencionadas.
    """
    cfg = _config_seam()
    path = cfg.get_config_path()
    if not path.is_file():
        raise ConfigUnavailable("config_missing",
                                f"config.yaml não existe em {path} — rode `hermes setup`.")
    if cfg.is_managed():
        raise ConfigUnavailable("managed",
                                "HERMES_HOME é um perfil gerenciado; escrita bloqueada (read-only).")

    raw = cfg.read_raw_config()
    if not isinstance(raw, dict):
        raise ConfigUnavailable("invalid", "config.yaml precisa ser um mapeamento YAML no topo.")

    # Backup datado antes de tocar no arquivo real.
    if backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(path, backup_dir / f"config-{stamp}.yaml")

    partial: Dict[str, Any] = {}
    applied: List[str] = []
    for path_key, payload in updates.items():
        parts = path_key.split(".")
        if not all(_VALID_KEY_RE.match(p) for p in parts):
            raise ConfigUnavailable("invalid_key", f"chave inválida: {path_key}")
        kind = payload.get("kind", "str") if isinstance(payload, dict) else None
        raw_value = payload.get("value") if isinstance(payload, dict) else payload
        try:
            value = _parse_leaf_value(path_key, kind or "str", raw_value)
        except ValueError as exc:
            raise ConfigUnavailable("invalid_value", str(exc)) from exc
        node = partial
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        applied.append(path_key)

    if not partial:
        return {"ok": True, "applied": [], "config_path": str(path)}

    _write_lock().acquire()
    try:
        cfg.save_config(partial, merge_existing=True)
    finally:
        _write_lock().release()
    return {"ok": True, "applied": applied, "config_path": str(path)}


class ConfigUnavailable(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


_lock: Optional[threading.RLock] = None
_lock_guard = threading.Lock()


def _write_lock() -> threading.RLock:
    global _lock
    if _lock is None:
        with _lock_guard:
            if _lock is None:
                _lock = threading.RLock()
    return _lock
