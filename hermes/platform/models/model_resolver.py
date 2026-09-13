import json
from pathlib import Path
from typing import Dict, Optional, Union

from hermes.platform.models.profiles import ModelProfile, ModelIdentity, ProviderRoute

# Concrete model bindings live in config (Emenda 26), never in schema/code.
# Path resolves to: hermes/platform/configs/defaults/model_profiles.json
DEFAULT_PROFILES_PATH = Path(__file__).resolve().parent.parent / "configs" / "defaults" / "model_profiles.json"


class UnknownModelProfileError(KeyError):
    """Raised when a requested model profile id is not registered.

    HAOS never silently substitutes a different model/profile: an unknown
    binding is a configuration error surfaced to the caller, not a hidden
    fallback.
    """


class ModelResolver:
    def __init__(self, profiles_path: Optional[Union[str, Path]] = None):
        self._profiles: Dict[str, ModelProfile] = {}
        self._load(Path(profiles_path) if profiles_path is not None else DEFAULT_PROFILES_PATH)

    def _load(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(
                f"Model profiles config not found: {path}. "
                f"HAOS resolves concrete models exclusively from config; "
                f"create the file or pass profiles_path explicitly."
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        profiles = data.get("model_profiles")
        if not isinstance(profiles, dict) or not profiles:
            raise ValueError(f"model_profiles config at {path} has no entries")
        for profile_id, cfg in profiles.items():
            identity_cfg = cfg.get("model_identity") or {}
            routes = [
                ProviderRoute(
                    provider_id=r["provider_id"],
                    provider_model_id=r["provider_model_id"],
                    priority=int(r.get("priority", 1)),
                )
                for r in (cfg.get("routes") or [])
            ]
            if not routes:
                raise ValueError(f"profile '{profile_id}' has no provider routes")
            self.register_profile(ModelProfile(
                id=profile_id,
                model_identity=ModelIdentity(
                    family=identity_cfg["family"],
                    variant=identity_cfg.get("variant", ""),
                    revision=identity_cfg.get("revision", "latest"),
                    strict_identity=bool(identity_cfg.get("strict_identity", True)),
                ),
                routes=routes,
                substitute_allowed=bool(cfg.get("substitute_allowed", False)),
                parameters=dict(cfg.get("parameters") or {}),
            ))

    def register_profile(self, profile: ModelProfile):
        self._profiles[profile.id] = profile

    def get(self, profile_id: str) -> Optional[ModelProfile]:
        return self._profiles.get(profile_id)

    def resolve(self, profile_id: str) -> ModelProfile:
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise UnknownModelProfileError(
                f"Unknown model profile '{profile_id}'. "
                f"Registered: {sorted(self._profiles)}"
            )
        return profile


def deduplicate_models(models: list) -> list:
    """Deduplicates a collection of model IDs or model dictionaries using a case-insensitive HashSet.

    Eliminates duplicates of bare model names (such as 'gpt-5.6-luna' shared across 'a6api' and 'codex')
    while preserving provider-prefixed variants (e.g. 'a6api_...', 'codex_...').
    """
    seen_ids: set[str] = set()
    deduped = []
    for item in models:
        mid = item["id"] if isinstance(item, dict) else item
        if not mid or not isinstance(mid, str):
            continue
        key = mid.strip().lower()
        if key in seen_ids:
            continue
        seen_ids.add(key)
        deduped.append(item)
    return deduped
