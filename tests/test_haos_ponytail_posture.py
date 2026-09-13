"""Tests for Ponytail Posture and Skills integration in HAOS."""

from pathlib import Path
import re

from hermes.platform.posture.specs import PostureResolver
from hermes.platform.memory.context_engine.compiler import ContextBuilder
from hermes.platform.tasks.spec import TaskSpec


def test_ponytail_posture_resolution():
    resolver = PostureResolver()
    posture = resolver.resolve("ponytail")
    assert posture.id == "ponytail"
    assert "Lazy Senior Developer" in posture.name
    assert "ponytail" in posture.skills_preferred
    assert "ponytail-review" in posture.skills_preferred


def test_ponytail_context_builder_overlay():
    resolver = PostureResolver()
    posture = resolver.resolve("ponytail")
    task = TaskSpec(id="T_TEST", title="Fix Datepicker", goal="Add minimal date input")

    builder = ContextBuilder()
    pkg = builder.build_package(task=task, posture=posture)

    overlay = pkg.sections["posture_overlay"].content
    assert "PONYTAIL DECISION LADDER" in overlay
    assert "YAGNI" in overlay
    assert "Stdlib does it" in overlay
    assert "Shortest working diff wins" in overlay


def test_ponytail_skill_authoring_standards():
    skills_dir = Path(__file__).resolve().parent.parent / "skills" / "development"
    for skill_name in ("ponytail", "ponytail-review"):
        skill_file = skills_dir / skill_name / "SKILL.md"
        assert skill_file.exists(), f"Missing {skill_file}"
        text = skill_file.read_text(encoding="utf-8")

        # Must have YAML frontmatter
        assert text.startswith("---")
        m = re.search(r"^description:\s*\"?([^\n\"]+)\"?", text, re.MULTILINE)
        assert m is not None, f"Missing description in {skill_name}"
        desc = m.group(1).strip()
        # HARDLINE: description <= 60 chars, ends with period
        assert len(desc) <= 60, f"Description too long ({len(desc)} chars): {desc}"
        assert desc.endswith("."), f"Description must end with period: {desc}"
