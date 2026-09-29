import pytest
from pathlib import Path
from hermes.platform.bots.identity import BotIdentitySpec
from hermes.platform.bots.identity_resolver import (
    IdentityResolver,
    PathTraversalError,
    resolve_safe_path,
)
from hermes.platform.bots.spec import BotSpec


def test_path_traversal_protection(tmp_path):
    base = tmp_path / "bot1"
    base.mkdir()

    # Allowed subpath
    assert resolve_safe_path(base, "SOUL.md") == base / "SOUL.md"

    # Traversal escape raises PathTraversalError
    with pytest.raises(PathTraversalError):
        resolve_safe_path(base, "../other.txt")
    with pytest.raises(PathTraversalError):
        resolve_safe_path(base, "/etc/passwd")


def test_identity_resolution_happy_path(tmp_path):
    bot_dir = tmp_path / "architect"
    bot_dir.mkdir()
    (bot_dir / "SOUL.md").write_text("Architect Core Soul", encoding="utf-8")
    (bot_dir / "IDENTITY.md").write_text("Ariadne Systems Architect", encoding="utf-8")
    (bot_dir / "VALUES.md").write_text("1. Modularity\n2. Safety", encoding="utf-8")

    spec = BotSpec(
        id="architect",
        name="Ariadne",
        identity=BotIdentitySpec(soul="SOUL.md", identity="IDENTITY.md", values="VALUES.md", version=1),
    )

    resolver = IdentityResolver(root_dir=tmp_path)
    bundle = resolver.resolve(spec, bot_dir=bot_dir)

    assert bundle.bot_id == "architect"
    assert bundle.identity_version == 1
    assert bundle.soul == "Architect Core Soul"
    assert bundle.identity == "Ariadne Systems Architect"
    assert bundle.values == "1. Modularity\n2. Safety"
    assert bundle.bundle_hash != ""

    # Cache hit
    cached = resolver.resolve(spec, bot_dir=bot_dir)
    assert cached is bundle

    # Invalidate cache
    resolver.invalidate_cache("architect")
    re_resolved = resolver.resolve(spec, bot_dir=bot_dir)
    assert re_resolved is not bundle
    assert re_resolved.bundle_hash == bundle.bundle_hash


def test_legacy_bot_fallback(tmp_path):
    spec = BotSpec(id="legacy-worker", name="Legacy Worker", description="Runs maintenance tasks")
    resolver = IdentityResolver(root_dir=tmp_path)
    bundle = resolver.resolve(spec)

    assert bundle.bot_id == "legacy-worker"
    assert bundle.identity_version == 1
    assert bundle.metadata.get("legacy_fallback") is True
    assert "Legacy Worker" in bundle.identity


def test_drift_detection(tmp_path):
    bot_dir = tmp_path / "coder"
    bot_dir.mkdir()
    soul_file = bot_dir / "SOUL.md"
    soul_file.write_text("Version 1 Coder", encoding="utf-8")

    spec = BotSpec(
        id="coder",
        name="CoderBot",
        identity=BotIdentitySpec(soul="SOUL.md", version=1),
    )
    resolver = IdentityResolver(root_dir=tmp_path)
    bundle = resolver.resolve(spec, bot_dir=bot_dir)

    # No drift initially
    assert not resolver.check_drift(bundle, spec.identity, bot_dir=bot_dir)

    # Modify file
    soul_file.write_text("Version 1 Coder [Modified]", encoding="utf-8")
    assert resolver.check_drift(bundle, spec.identity, bot_dir=bot_dir)


def test_diff_bundles():
    spec = BotSpec(id="bot", name="Bot")
    resolver = IdentityResolver()
    b1 = resolver.resolve(spec)

    b2 = b1.__class__(
        bot_id="bot",
        identity_version=2,
        soul="New Soul Content",
        identity=b1.identity,
        values=b1.values,
    )

    diff = IdentityResolver.diff_bundles(b1, b2)
    assert diff["has_changes"] is True
    assert "soul" in diff["diffs"]
    assert "+New Soul Content" in diff["diffs"]["soul"]
