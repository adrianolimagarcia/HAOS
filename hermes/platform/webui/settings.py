"""HAOS Standalone WebUI — configuração persistente do engine (settings.json).

A UI standalone expõe configurações OPERACIONAIS do Task Engine (limites de
concorrência globais/por-provider/por-modelo e comportamento do scheduler).
Estas configurações NÃO são segredos e não pertencem ao ``config.yaml`` do
agente (que é do agente, não do engine): ficam em ``settings.json`` dentro do
``data_dir`` do standalone (default ``~/.haos/settings.json``).

Princípios:
* Toda persistência é um JSON pequeno e versionado (``schema``).
* ``load_settings`` nunca falha: ausência/invalidez => defaults honestos.
* ``apply_to_guard`` aplica o settings AO VIVO no ``ConcurrencyGuard``
  (atributos e dicts mutáveis, protegidos por RLock do próprio guard).
* Coerção estrita: inteiros >= 1, dicts de ints, chaves lowercase.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

# Chaves validáveis com tipos/coerções (semântica idêntica ao ConcurrencyGuard).
_SCHEMA_VERSION = 1

_DEFAULTS: Dict[str, Any] = {
    "max_global_concurrency": 8,
    "default_provider_limit": 2,
    "provider_limits": {"a6api": 4, "openai": 4, "anthropic": 4, "fallback": 2},
    "model_limits": {},
    "default_model_limit": None,
    "poll_refresh_ms": 4000,
    "shadow_mode": True,  # Ouroboros em PROPOSAL_ONLY — nunca aplica sozinho
    "auto_dispatch": True,  # Despacha cards READY automaticamente em cascata
}

_LOCK = threading.RLock()


def default_settings() -> Dict[str, Any]:
    return json.loads(json.dumps(_DEFAULTS))


def _coerce_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _coerce_limits(raw: Any) -> Dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, int] = {}
    for key, val in raw.items():
        if isinstance(key, str) and key.strip():
            out[key.strip().lower()] = _coerce_int(val, 2)
    return out


def validate(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza/coage um dict arbitrário para o shape canônico (fail-closed)."""
    base = default_settings()
    if not isinstance(settings, dict):
        return base
    out: Dict[str, Any] = {}
    out["max_global_concurrency"] = _coerce_int(
        settings.get("max_global_concurrency"), base["max_global_concurrency"])
    out["default_provider_limit"] = _coerce_int(
        settings.get("default_provider_limit"), base["default_provider_limit"])
    out["provider_limits"] = dict(base["provider_limits"])
    if isinstance(settings.get("provider_limits"), dict):
        out["provider_limits"].update(_coerce_limits(settings["provider_limits"]))
    out["model_limits"] = _coerce_limits(settings.get("model_limits"))
    mdl = settings.get("default_model_limit")
    out["default_model_limit"] = (
        _coerce_int(mdl, 2) if mdl not in (None, "", 0) else None)
    out["poll_refresh_ms"] = _coerce_int(settings.get("poll_refresh_ms"), 4000)
    shadow = settings.get("shadow_mode", True)
    out["shadow_mode"] = bool(shadow) if isinstance(shadow, bool) else True
    auto_disp = settings.get("auto_dispatch", True)
    out["auto_dispatch"] = bool(auto_disp) if isinstance(auto_disp, bool) else True
    out["schema"] = _SCHEMA_VERSION
    return out


def settings_path(data_dir: Path) -> Path:
    return data_dir / "settings.json"


def load_settings(data_dir: Path) -> Dict[str, Any]:
    """Carrega settings persistidos; ausente/corrompido => defaults (nunca lança)."""
    path = settings_path(data_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        loaded = validate(raw)
        if loaded.get("schema", 0) != _SCHEMA_VERSION:
            return default_settings()
        loaded.pop("schema", None)
        return loaded
    except Exception:
        return default_settings()


def save_settings(data_dir: Path, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Mescla *patch* sobre o persistido, valida e grava (atomic replace)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        merged = dict(load_settings(data_dir))
        merged.update(patch if isinstance(patch, dict) else {})
        normalized = validate(merged)
        path = settings_path(data_dir)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(normalized, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(path)
        return normalized


def reset_settings(data_dir: Path) -> Dict[str, Any]:
    """Remove o arquivo persistido e devolve defaults (não toca o guard vivo)."""
    path = settings_path(data_dir)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return default_settings()


def apply_to_guard(guard: Any, settings: Dict[str, Any]) -> None:
    """Aplica settings AO VIVO num ConcurrencyGuard (mutações sob o lock dele).

    ``guard`` nunca é None aqui: o servidor sempre constrói um guard. As
    mutações respeitam a estrutura interna do guard (dicts de limites em
    lowercase + max_global/max_active espelhados).
    """
    if guard is None or not isinstance(settings, dict):
        return
    norm = validate(settings)
    with guard._lock:  # noqa: SLF001 — lock interno do ConcurrencyGuard (reentrante)
        guard.max_global_concurrency = norm["max_global_concurrency"]
        guard.max_active_workers = norm["max_global_concurrency"]
        guard.default_provider_limit = norm["default_provider_limit"]
        for key, val in norm["provider_limits"].items():
            guard.provider_limits[key] = max(1, val)
        guard.model_limits = {k: max(1, v) for k, v in norm["model_limits"].items()}
        guard.default_model_limit = norm["default_model_limit"]
