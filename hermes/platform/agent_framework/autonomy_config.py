"""Validated profile configuration for the opt-in event consumer."""
from dataclasses import dataclass
import fnmatch


@dataclass(frozen=True)
class AutonomyConfig:
    enabled: bool = False
    auto_apply: bool = False
    poll_seconds: int = 15
    telemetry_seconds: int = 60
    cooldown_seconds: int = 300
    max_pending: int = 128
    max_jobs_per_hour: int = 6
    investigation_timeout: int = 90
    max_output_tokens: int = 2048
    event_patterns: tuple[str, ...] = ("*.failed", "*.error", "*.warning")

    @classmethod
    def load(cls):
        from hermes_cli.config_effective import load_user_config_effective
        root = load_user_config_effective().get("framework", {})
        if not isinstance(root, dict):
            raise ValueError("framework must be a mapping")
        raw = root.get("autonomy", {})
        if not isinstance(raw, dict) or set(raw) - set(cls.__dataclass_fields__):
            raise ValueError("Invalid framework.autonomy configuration")
        values = {name: raw.get(name, field.default) for name, field in cls.__dataclass_fields__.items()}
        for name in ("enabled", "auto_apply"):
            if type(values[name]) is not bool:
                raise ValueError(f"{name} must be a boolean")
        bounds = {"poll_seconds": (1, 120), "telemetry_seconds": (15, 3600),
                  "cooldown_seconds": (30, 86400), "max_pending": (1, 128),
                  "max_jobs_per_hour": (1, 24), "investigation_timeout": (10, 180),
                  "max_output_tokens": (256, 4096)}
        for name, (minimum, maximum) in bounds.items():
            value = values[name]
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
        patterns = values["event_patterns"]
        if not isinstance(patterns, (list, tuple)) or not 1 <= len(patterns) <= 16 or any(
                not isinstance(p, str) or not p or len(p) > 128 for p in patterns):
            raise ValueError("event_patterns must contain 1..16 bounded patterns")
        values["event_patterns"] = tuple(patterns)
        return cls(**values)

    def matches(self, event_type: str) -> bool:
        # Internal outcome events never become new investigations, even with wildcard config.
        return not event_type.startswith(("pipeline.", "autonomy.")) and any(
            fnmatch.fnmatchcase(event_type, pattern) for pattern in self.event_patterns)
