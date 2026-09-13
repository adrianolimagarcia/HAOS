import hashlib
import json
import uuid
from typing import Dict, Any, Optional
from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.posture.specs import PostureSpec
from hermes.platform.memory.context_engine.builder import ContextPackage, SectionContent

class ContextBuilder:
    def __init__(self, token_budget_limit: int = 64000):
        self.token_budget_limit = token_budget_limit

    def build_package(self, task: TaskSpec, posture: PostureSpec,
                      obsidian_docs: Optional[Dict[str, Any]] = None,
                      lsp_symbols: Optional[Dict[str, Any]] = None,
                      untrusted_web_data: Optional[Dict[str, Any]] = None) -> ContextPackage:

        ctx_id = f"ctx-{uuid.uuid4().hex[:8]}"
        sections: Dict[str, SectionContent] = {}

        # 1. System Constitution (Core Policy 1.0)
        sections["system_constitution"] = SectionContent(
            trust_level="core_policy",
            content="HAOS System Constitution: You operate under Hermes AIAgent Kernel. Maintain security and truth."
        )

        # 2. Posture Overlay
        overlay_content = f"Posture {posture.name}: {posture.description}. Overlays: {posture.prompt_overlay}"
        if posture.id == "ponytail" or "posture.ponytail" in posture.prompt_overlay:
            overlay_content += (
                "\n[PONYTAIL DECISION LADDER - LAZY SENIOR DEV]\n"
                "Stop at the first rung that holds:\n"
                "1. Does this need to exist? -> No: skip it (YAGNI).\n"
                "2. Already in codebase? -> Reuse it, don't rewrite.\n"
                "3. Stdlib does it? -> Use standard library primitives.\n"
                "4. Native platform/browser feature? -> Use native HTML/CSS/OS features.\n"
                "5. Installed dependency? -> Use it, zero new packages.\n"
                "6. Can it be one line? -> One line.\n"
                "7. Only then: minimum working code.\n"
                "Rules: No unrequested abstractions. No boilerplate. Deletion over addition. Shortest working diff wins.\n"
                "Never cut: trust-boundary validation, security, data-loss prevention, accessibility."
            )
        sections["posture_overlay"] = SectionContent(
            trust_level="system",
            content=overlay_content
        )

        # 3. Task Intent
        sections["task_intent"] = SectionContent(
            trust_level="system",
            content={"goal": task.goal, "description": task.description, "acceptance": task.acceptance_criteria}
        )

        # 4. Canonical Obsidian (0.90)
        if obsidian_docs:
            sections["canonical_obsidian"] = SectionContent(
                trust_level="canonical_obsidian",
                content=obsidian_docs
            )

        # 5. LSP & Code Intelligence (0.90)
        if lsp_symbols:
            sections["code_intelligence"] = SectionContent(
                trust_level="internal",
                content=lsp_symbols
            )

        # 6. Untrusted Data (0.20) - Sanitized as Data Only
        if untrusted_web_data:
            sections["untrusted_external"] = SectionContent(
                trust_level="untrusted_external",
                content={"DATA_ONLY": untrusted_web_data}
            )

        # Compute hash
        content_str = json.dumps({k: v.content for k, v in sections.items()}, default=str)
        digest = hashlib.sha256(content_str.encode()).hexdigest()

        return ContextPackage(
            id=ctx_id,
            task_id=task.id,
            task_revision=task.version,
            posture_id=posture.id,
            sections=sections,
            token_count=len(content_str) // 4,
            token_budget_limit=self.token_budget_limit,
            digest_hash=digest
        )
