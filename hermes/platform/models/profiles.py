from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class ModelIdentity:
    family: str # e.g. deepseek-v4, claude-3-5, gpt-4o
    variant: str # e.g. flash, sonnet
    revision: str = "latest"
    strict_identity: bool = True

@dataclass
class ProviderRoute:
    provider_id: str
    provider_model_id: str
    priority: int = 1

@dataclass
class ModelProfile:
    id: str
    model_identity: ModelIdentity
    routes: List[ProviderRoute] = field(default_factory=list)
    substitute_allowed: bool = False
    parameters: Dict[str, Any] = field(default_factory=dict)
