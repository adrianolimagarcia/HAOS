"""Agent Policy Engine governing security tiers, whitelisting, and execution gates.

Implements security boundaries and evaluation for plan steps across SAFE, MODERATE,
and CRITICAL risk tiers with YAML persistence and robust built-in fallbacks.
"""

from __future__ import annotations

import copy
import fnmatch
import json
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None

from hermes.platform.agent_framework.models import PlanStep, RiskTier

logger = logging.getLogger(__name__)

_RISK_RANK: dict[RiskTier, int] = {
    RiskTier.SAFE: 10,
    RiskTier.MODERATE: 20,
    RiskTier.CRITICAL: 30,
}


def max_risk_tier(t1: RiskTier, t2: RiskTier) -> RiskTier:
    """Return the higher risk tier between two tiers."""
    r1 = _RISK_RANK.get(t1, 10)
    r2 = _RISK_RANK.get(t2, 10)
    return t1 if r1 >= r2 else t2


@dataclass
class PolicyDecision:
    """Outcome of evaluating a plan step against the security policy."""

    allowed: bool
    requires_confirmation: bool
    reason: str
    risk_tier: RiskTier

    def to_dict(self) -> dict[str, Any]:
        """Convert decision to primitive dictionary."""
        return {
            "allowed": self.allowed,
            "requires_confirmation": self.requires_confirmation,
            "reason": self.reason,
            "risk_tier": self.risk_tier.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PolicyDecision:
        """Create PolicyDecision from dictionary."""
        risk = data.get("risk_tier", RiskTier.SAFE)
        if isinstance(risk, str):
            risk = RiskTier(risk.lower())
        return cls(
            allowed=bool(data.get("allowed", False)),
            requires_confirmation=bool(data.get("requires_confirmation", False)),
            reason=str(data.get("reason", "")),
            risk_tier=risk,
        )


DEFAULT_POLICY_RULES: dict[str, Any] = {
    "version": 1,
    "strict_whitelist": True,
    "tiers": {
        "safe": ["read_telemetry", "recommend", "log_audit", "noop"],
        "moderate": ["wal_checkpoint_passive", "wal_checkpoint", "bdi_limit_device",
                     "bdi_tune", "drop_caches", "restart_service", "config_change",
                     "wal_checkpoint_truncate", "process_renice", "sysctl_tune",
                     "throttle_io", "fan_speed_boost"],
        "critical": ["disk_format", "partition_delete", "system_reboot", "forced_reboot",
                     "firewall_flush", "rm_rf", "shell_cmd_destructive", "shell_cmd_safe"],
    },
    "action_grants": [],
    "whitelist": ["read_telemetry", "recommend", "log_audit", "noop", "wal_checkpoint_passive"],
    "blacklist": {
        "actions": ["disk_format", "partition_delete", "system_reboot", "forced_reboot",
                    "firewall_flush", "rm_rf", "shell_cmd_destructive", "shell_cmd_safe"],
        "patterns": ["*rm -rf*", "*mkfs*", "*dd if=*", "*iptables -F*"],
        "targets": ["/", "/boot", "/dev/sda", "/dev/nvme0n1"],
    },
}

# Configuration may raise risk, but cannot make these host mutations inherently safe.
_MUTATION_ACTIONS = frozenset(DEFAULT_POLICY_RULES["tiers"]["moderate"])
_FORBIDDEN_ACTIONS = frozenset(DEFAULT_POLICY_RULES["blacklist"]["actions"])


class AgentPolicyEngine:
    """Policy engine enforcing security tiers, whitelisting, and execution gates.

    Loads rules from a YAML configuration file or defaults to built-in rules.
    Provides deterministic gating for autonomous versus assisted execution.
    """

    def __init__(
        self,
        policy_path: Path | str | None = None,
        auto_load: bool = True,
    ) -> None:
        """Initialize the policy engine.

        Args:
            policy_path: Path to agent-policy.yaml. If None, resolves to
                         HERMES_HOME/agent/agent-policy.yaml with fallback.
            auto_load: Whether to load policy from disk if file exists.
        """
        if policy_path is not None:
            self.policy_path = Path(policy_path).absolute()
        else:
            from hermes_constants import get_hermes_home

            self.policy_path = (Path(get_hermes_home()) / "agent" / "agent-policy.yaml").absolute()

        # Preserve lexical identity so a link cannot redirect policy into another profile.
        from .state_manager import HAOSStateManager
        HAOSStateManager._check_path(self.policy_path)

        self._policy_data: dict[str, Any] = copy.deepcopy(DEFAULT_POLICY_RULES)
        self._action_tiers: dict[str, RiskTier] = {}
        self._whitelist: set[str] = set()
        self._blacklist_actions: set[str] = set()
        self._blacklist_targets: set[str] = set()
        self._blacklist_patterns: list[str] = []
        self._strict_whitelist: bool = False

        self._rebuild_indices()

        self._load_failed = False
        if auto_load and self.policy_path.is_file():
            self._load_failed = not self.load_from_yaml(self.policy_path)

    @staticmethod
    def _validate(data: dict[str, Any]) -> None:
        for container, keys in (("tiers", ("safe", "moderate", "critical")),
                                ("blacklist", ("actions", "patterns", "targets"))):
            if not isinstance(data.get(container), dict):
                raise ValueError(f"Invalid {container}")
            for key in keys:
                values = data[container].get(key)
                if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                    raise ValueError(f"Invalid {container}.{key}")
        grants = data.get("action_grants", [])
        if not isinstance(grants, list):
            raise ValueError("Invalid action_grants")
        for grant in grants:
            if (not isinstance(grant, dict) or set(grant) != {"action", "target", "params", "autonomous", "allow_rollback"}
                    or not isinstance(grant["action"], str) or not isinstance(grant["target"], str)
                    or not isinstance(grant["params"], dict)
                    or type(grant["autonomous"]) is not bool or type(grant["allow_rollback"]) is not bool):
                raise ValueError("Invalid exact action grant")
            from .approvals import canonical
            canonical(grant["params"])
        values = data.get("whitelist")
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError("Invalid whitelist")

    def _rebuild_indices(self) -> None:
        """Rebuild internal lookup tables and sets from _policy_data."""
        tiers_data = self._policy_data.get("tiers", {})
        self._action_tiers.clear()

        for act in tiers_data.get("safe", []):
            self._action_tiers[act.lower()] = RiskTier.SAFE
        for act in tiers_data.get("moderate", []):
            self._action_tiers[act.lower()] = RiskTier.MODERATE
        for act in tiers_data.get("critical", []):
            self._action_tiers[act.lower()] = RiskTier.CRITICAL

        self._whitelist = {w.lower() for w in self._policy_data.get("whitelist", [])}

        bl = self._policy_data.get("blacklist", {})
        self._blacklist_actions = {a.lower() for a in bl.get("actions", [])}
        self._blacklist_targets = {t.lower() for t in bl.get("targets", [])}
        self._blacklist_patterns = [str(p) for p in bl.get("patterns", [])]

        self._strict_whitelist = bool(self._policy_data.get("strict_whitelist", False))

    def load_from_yaml(self, path: Path | str | None = None) -> bool:
        """Load policy rules from a YAML file.

        Args:
            path: Target file path. Defaults to self.policy_path.

        Returns:
            True if loaded successfully, False otherwise.
        """
        target = Path(path).absolute() if path is not None else self.policy_path
        self._load_failed = True
        from .state_manager import HAOSStateManager
        HAOSStateManager._check_path(target)
        if not target.is_file():
            return False

        try:
            content = target.read_text(encoding="utf-8").strip()
            if not content:
                return False

            if yaml is not None:
                parsed = yaml.safe_load(content)
            else:
                parsed = json.loads(content)

            if isinstance(parsed, dict):
                candidate = copy.deepcopy(DEFAULT_POLICY_RULES)
                for key, val in parsed.items():
                    if isinstance(val, dict) and isinstance(candidate.get(key), dict):
                        candidate[key].update(val)
                    else:
                        candidate[key] = val
                self._validate(candidate)
                self._policy_data = candidate
                self._rebuild_indices()
                self._load_failed = False
                return True
        except Exception as exc:
            logger.warning("Failed to parse policy YAML at %s: %s", target, exc)

        return False

    def save_to_yaml(self, path: Path | str | None = None) -> None:
        """Persist current policy rules to a YAML file atomically."""
        target = Path(path).absolute() if path is not None else self.policy_path
        from .state_manager import HAOSStateManager
        HAOSStateManager._check_path(target)
        from hermes_constants import mkdir_under_hermes_home
        mkdir_under_hermes_home(target.parent)
        tmp_path = target.with_name(f"{target.name}.tmp.{uuid.uuid4().hex}")
        try:
            if yaml is not None:
                rendered = yaml.safe_dump(self._policy_data, default_flow_style=False, sort_keys=False)
            else:
                rendered = json.dumps(self._policy_data, indent=2, sort_keys=False)
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(rendered)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, target)
            directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def update_policy(self, update_dict: dict[str, Any], persist: bool = False) -> None:
        """Update policy configurations and rebuild index tables."""
        candidate = copy.deepcopy(self._policy_data)
        for key, val in update_dict.items():
            if isinstance(val, dict) and isinstance(candidate.get(key), dict):
                candidate[key].update(val)
            else:
                candidate[key] = val
        self._validate(candidate)
        self._policy_data = candidate
        self._rebuild_indices()
        if persist:
            self.save_to_yaml()

    def get_action_tier(self, action_name: str, default: RiskTier = RiskTier.CRITICAL) -> RiskTier:
        """Get the configured risk tier for an action name."""
        return self._action_tiers.get(action_name.lower().strip(), default)

    def set_action_tier(self, action_name: str, tier: RiskTier, persist: bool = False) -> None:
        """Assign an action to a specific security risk tier."""
        clean_name = action_name.lower().strip()
        self._action_tiers[clean_name] = tier

        # Update in _policy_data
        tiers = self._policy_data.setdefault("tiers", {})
        for t_name in ("safe", "moderate", "critical"):
            lst = tiers.setdefault(t_name, [])
            if clean_name in [x.lower() for x in lst]:
                lst[:] = [x for x in lst if x.lower() != clean_name]

        tiers.setdefault(tier.value, []).append(clean_name)
        if persist:
            self.save_to_yaml()

    def is_whitelisted(self, action_name: str) -> bool:
        """Check whether an action is in the whitelist."""
        return action_name.lower().strip() in self._whitelist

    def add_to_whitelist(self, action_name: str, persist: bool = False) -> None:
        """Add an action to the approved whitelist."""
        clean_name = action_name.lower().strip()
        self._whitelist.add(clean_name)
        wl = self._policy_data.setdefault("whitelist", [])
        if clean_name not in [x.lower() for x in wl]:
            wl.append(clean_name)
        if persist:
            self.save_to_yaml()

    def remove_from_whitelist(self, action_name: str, persist: bool = False) -> None:
        """Remove an action from the approved whitelist."""
        clean_name = action_name.lower().strip()
        self._whitelist.discard(clean_name)
        wl = self._policy_data.setdefault("whitelist", [])
        wl[:] = [x for x in wl if x.lower() != clean_name]
        if persist:
            self.save_to_yaml()

    def add_to_blacklist(self, pattern_or_action: str, persist: bool = False) -> None:
        """Add an action, target, or pattern to the blacklist."""
        clean = pattern_or_action.strip()
        bl = self._policy_data.setdefault("blacklist", {})
        actions = bl.setdefault("actions", [])
        if clean.lower() not in [x.lower() for x in actions]:
            actions.append(clean)
        self._rebuild_indices()
        if persist:
            self.save_to_yaml()

    def remove_from_blacklist(self, pattern_or_action: str, persist: bool = False) -> None:
        """Remove an item from the blacklist actions/patterns."""
        clean = pattern_or_action.lower().strip()
        bl = self._policy_data.setdefault("blacklist", {})
        if "actions" in bl:
            bl["actions"] = [x for x in bl["actions"] if x.lower() != clean]
        if "patterns" in bl:
            bl["patterns"] = [p for p in bl["patterns"] if p.lower() != clean]
        if "targets" in bl:
            bl["targets"] = [t for t in bl["targets"] if t.lower() != clean]
        self._rebuild_indices()
        if persist:
            self.save_to_yaml()

    def is_blacklisted(self, step: PlanStep) -> tuple[bool, str]:
        """Check if a plan step matches any blacklist rule.

        Returns:
            Tuple of (is_blacklisted, reason_string).
        """
        action_clean = step.action_name.lower().strip()
        target_clean = step.target.lower().strip()

        # 1. Action name check
        if action_clean in self._blacklist_actions:
            return True, f"Action '{step.action_name}' is in the policy blacklist"

        # 2. Target check
        if target_clean in self._blacklist_targets:
            return True, f"Target '{step.target}' is in the policy blacklist"

        # 3. Pattern matching against action and target
        for pattern in self._blacklist_patterns:
            if fnmatch.fnmatchcase(action_clean, pattern.lower()):
                return True, f"Action '{step.action_name}' matches blacklisted pattern '{pattern}'"
            if fnmatch.fnmatchcase(target_clean, pattern.lower()):
                return True, f"Target '{step.target}' matches blacklisted pattern '{pattern}'"

        # 4. Check parameter strings against blacklist patterns
        for param_val in step.params.values():
            val_str = str(param_val).lower()
            for pattern in self._blacklist_patterns:
                # Direct wildcard match or substring match for safety
                pat_core = pattern.strip("*").lower()
                if pat_core and pat_core in val_str:
                    return True, f"Parameter value '{param_val}' contains blacklisted token '{pat_core}'"
                if fnmatch.fnmatchcase(val_str, pattern.lower()):
                    return True, f"Parameter value matches blacklisted pattern '{pattern}'"

        return False, ""

    def evaluate_step(
        self,
        step: PlanStep,
        dry_run: bool = False,
        autonomous: bool = False,
    ) -> PolicyDecision:
        """Gate actions; confirmation is a blocker, never an approval boolean.

        Unknown names fail closed even when strict_whitelist is configured false.
        Dry-run permission only permits describing a known action, not executing it.
        """
        if self._load_failed:
            return PolicyDecision(False, True, "Policy load failed; execution disabled", RiskTier.CRITICAL)
        action = step.action_name.lower().strip()
        configured = self.get_action_tier(action, default=RiskTier.CRITICAL)
        tier = max_risk_tier(configured, step.risk_tier)
        if action in _MUTATION_ACTIONS:
            tier = max_risk_tier(tier, RiskTier.MODERATE)
        blocked, reason = self.is_blacklisted(step)
        if action in _FORBIDDEN_ACTIONS or blocked:
            return PolicyDecision(False, True, reason or "Destructive and shell actions forbidden", RiskTier.CRITICAL)
        if action not in self._action_tiers or not self.is_whitelisted(action):
            return PolicyDecision(False, True, "Unknown or non-whitelisted action", tier)
        if dry_run:
            return PolicyDecision(True, tier != RiskTier.SAFE, "Describe only; no execution", tier)
        if tier != RiskTier.SAFE:
            return PolicyDecision(False, True, "Operator confirmation required; execution blocked", tier)
        return PolicyDecision(True, False, "Registered SAFE action permitted", tier)

    def action_grant(self, step: PlanStep, *, autonomous: bool = False, rollback: bool = False) -> bool:
        """Exact grants cannot install handlers or weaken immutable host-action gates."""
        from .approvals import canonical
        if self._load_failed or step.action_name != "workspace_config_update":
            return False
        blocked, _ = self.is_blacklisted(step)
        if blocked or step.risk_tier == RiskTier.CRITICAL:
            return False
        for grant in self._policy_data.get("action_grants", []):
            if (grant.get("action") == step.action_name and grant.get("target") == step.target
                    and canonical(grant.get("params")) == canonical(step.params)
                    and (not autonomous or grant.get("autonomous") is True)
                    and (not rollback or grant.get("allow_rollback") is True)):
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._policy_data)
