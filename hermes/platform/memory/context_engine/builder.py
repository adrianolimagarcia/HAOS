from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class SectionContent:
    trust_level: str # core_policy | canonical_obsidian | internal | untrusted_external
    content: Any

@dataclass
class ContextPackage:
    id: str
    task_id: str
    task_revision: int
    posture_id: str
    sections: Dict[str, SectionContent] = field(default_factory=dict)
    token_count: int = 0
    token_budget_limit: int = 64000
    digest_hash: str = ""
