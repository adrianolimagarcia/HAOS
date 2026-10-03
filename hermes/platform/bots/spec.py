"""BotSpec and declarative trigger definitions."""
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Literal

@dataclass(frozen=True)
class BotPolicy:
    permissions: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    workspace: Optional[str] = None
    max_cost_usd: Optional[float] = None
    max_runtime_minutes: Optional[int] = None
    required_grants: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_cost_usd is not None and self.max_cost_usd < 0: raise ValueError("max_cost_usd must be non-negative")
        if self.max_runtime_minutes is not None and self.max_runtime_minutes < 1: raise ValueError("max_runtime_minutes must be positive")
        if self.workspace is not None and not self.workspace.strip(): raise ValueError("workspace cannot be empty")

    def to_dict(self) -> Dict[str, Any]: return asdict(self)

@dataclass(frozen=True)
class TriggerSpec:
    id: str
    type: Literal["manual", "cron", "webhook"]
    name: str = ""
    cron: Optional[str] = None
    auth_token: Optional[str] = None
    event_name: Optional[str] = None
    def __post_init__(self) -> None:
        if not self.id.strip(): raise ValueError("TriggerSpec id is required")
        if self.type not in ("manual", "cron", "webhook"): raise ValueError("invalid trigger type")
        if self.type == "cron" and (not self.cron or len(self.cron.split()) != 5): raise ValueError("cron triggers require a five-field expression")
        if self.type == "webhook" and (not self.auth_token or not self.event_name): raise ValueError("webhook triggers require auth_token and event_name")
        if self.type != "cron" and self.cron is not None: raise ValueError("only cron triggers accept cron")
    def to_dict(self) -> Dict[str, Any]: return asdict(self)
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TriggerSpec": return cls(**{k: data[k] for k in ("id", "type", "name", "cron", "auth_token", "event_name") if k in data})

@dataclass(frozen=True)
class BotSpec:
    id: str
    name: str
    description: str = ""
    task_defaults: Dict[str, Any] = field(default_factory=dict)
    capabilities: List[str] = field(default_factory=list)
    policy: BotPolicy = field(default_factory=BotPolicy)
    version: int = 1
    routines: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    triggers: List[TriggerSpec] = field(default_factory=list)
    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip(): raise ValueError("BotSpec id and name are required")
        if self.version < 1: raise ValueError("BotSpec version must be positive")
        ids = [t.id for t in self.triggers]
        if len(ids) != len(set(ids)): raise ValueError("trigger ids must be unique")
    def to_dict(self) -> Dict[str, Any]: return asdict(self)
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BotSpec":
        values = {k: data[k] for k in ("id", "name", "description", "task_defaults", "capabilities", "policy", "version", "routines", "triggers") if k in data}
        if isinstance(values.get("policy"), dict): values["policy"] = BotPolicy(**values["policy"])
        values["triggers"] = [TriggerSpec.from_dict(x) if isinstance(x, dict) else x for x in values.get("triggers", [])]
        return cls(**values)
