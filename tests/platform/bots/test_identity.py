import pytest
from hermes.platform.bots.identity import (
    BotIdentitySpec,
    BotIdentityBundle,
    IdentityVersion,
    LeafIdentitySnapshot,
    CouncilSpec,
    DecisionRecord,
    compute_sha256,
    compute_bundle_hash,
)
from hermes.platform.bots.spec import BotSpec, BotPolicy


def test_bot_identity_spec_defaults():
    spec = BotIdentitySpec()
    assert spec.soul == "SOUL.md"
    assert spec.identity == "IDENTITY.md"
    assert spec.values == "VALUES.md"
    assert spec.version == 1
    d = spec.to_dict()
    assert BotIdentitySpec.from_dict(d) == spec


def test_bot_identity_spec_custom():
    spec = BotIdentitySpec(soul="custom_soul.md", identity="custom_id.md", values="custom_val.md", version=3)
    assert spec.soul == "custom_soul.md"
    assert spec.version == 3
    assert BotIdentitySpec.from_dict(spec.to_dict()) == spec


def test_bot_identity_bundle_hashes():
    bundle = BotIdentityBundle(
        bot_id="architect",
        identity_version=1,
        soul="Core Architect Soul",
        identity="Architect v1",
        values="Simplicity > Complexity",
    )
    assert bundle.soul_hash == compute_sha256("Core Architect Soul")
    assert bundle.identity_hash == compute_sha256("Architect v1")
    assert bundle.values_hash == compute_sha256("Simplicity > Complexity")
    assert bundle.bundle_hash == compute_bundle_hash(
        "Core Architect Soul", "Architect v1", "Simplicity > Complexity"
    )

    # Roundtrip
    d = bundle.to_dict()
    restored = BotIdentityBundle.from_dict(d)
    assert restored.bot_id == bundle.bot_id
    assert restored.bundle_hash == bundle.bundle_hash
    assert restored.soul == bundle.soul


def test_bot_identity_bundle_validation():
    with pytest.raises(ValueError, match="bot_id is required"):
        BotIdentityBundle(bot_id="  ")


def test_identity_version_immutability_and_roundtrip():
    bundle = BotIdentityBundle(bot_id="bot-1", soul="Soul", identity="Id", values="Val")
    v = IdentityVersion(
        id="ver-1",
        bot_id="bot-1",
        version=1,
        bundle_hash=bundle.bundle_hash,
        bundle=bundle,
        status="active",
    )
    assert v.version == 1
    assert v.status == "active"
    d = v.to_dict()
    restored = IdentityVersion.from_dict(d)
    assert restored.id == v.id
    assert restored.bundle is not None
    assert restored.bundle.bundle_hash == bundle.bundle_hash


def test_identity_version_validation():
    with pytest.raises(ValueError, match="IdentityVersion id is required"):
        IdentityVersion(id="", bot_id="b", version=1, bundle_hash="abc")
    with pytest.raises(ValueError, match="bot_id is required"):
        IdentityVersion(id="1", bot_id="", version=1, bundle_hash="abc")
    with pytest.raises(ValueError, match="version must be positive"):
        IdentityVersion(id="1", bot_id="b", version=0, bundle_hash="abc")


def test_leaf_identity_snapshot():
    snap = LeafIdentitySnapshot(
        leaf_id="leaf-123",
        parent_bot_id="bot-architect",
        identity_version_id="ver-1",
        bot_identity_version=1,
        identity_bundle_hash="bundle-hash-1",
        temporary_soul="Temporary mission soul for db design",
        prompt_hash="prompt-hash-1",
        toolset_hash="toolset-hash-1",
        correlation_id="corr-1",
    )
    assert snap.temporary_soul_hash == compute_sha256("Temporary mission soul for db design")
    d = snap.to_dict()
    restored = LeafIdentitySnapshot.from_dict(d)
    assert restored.leaf_id == snap.leaf_id
    assert restored.parent_bot_id == snap.parent_bot_id
    assert restored.temporary_soul_hash == snap.temporary_soul_hash


def test_leaf_identity_snapshot_validation():
    with pytest.raises(ValueError, match="leaf_id is required"):
        LeafIdentitySnapshot(leaf_id="", parent_bot_id="p", identity_version_id="v")
    with pytest.raises(ValueError, match="parent_bot_id is required"):
        LeafIdentitySnapshot(leaf_id="l", parent_bot_id="", identity_version_id="v")
    with pytest.raises(ValueError, match="identity_version_id is required"):
        LeafIdentitySnapshot(leaf_id="l", parent_bot_id="p", identity_version_id="")


def test_council_spec():
    spec = CouncilSpec(
        id="arch-council",
        purpose="Review architecture decisions",
        members=["architect", "researcher", "engineer"],
        roles={"architect": "lead", "engineer": "implementer"},
        decision_mode="consensus_with_dissent",
    )
    assert spec.id == "arch-council"
    assert len(spec.members) == 3
    d = spec.to_dict()
    restored = CouncilSpec.from_dict(d)
    assert restored == spec


def test_council_spec_invalid_mode():
    with pytest.raises(ValueError, match="invalid decision_mode"):
        CouncilSpec(id="c", purpose="p", decision_mode="invalid_mode")


def test_decision_record():
    rec = DecisionRecord(
        id="dec-1",
        council_id="arch-council",
        council_session_id="sess-1",
        objective="Choose database architecture",
        participants=["architect", "engineer"],
        identity_version_refs={"architect": "v-1", "engineer": "v-2"},
        positions={"architect": "prefer SQLite WAL", "engineer": "concur"},
        synthesis="Adopt SQLite WAL with periodic checkpointing",
        dissent={},
        decision="approved",
    )
    assert rec.decision == "approved"
    d = rec.to_dict()
    restored = DecisionRecord.from_dict(d)
    assert restored.id == rec.id
    assert restored.synthesis == rec.synthesis


def test_botspec_with_identity_roundtrip():
    # Legacy BotSpec without identity
    legacy = BotSpec(id="leg", name="LegacyBot")
    assert legacy.identity is None
    d_leg = legacy.to_dict()
    assert BotSpec.from_dict(d_leg).identity is None

    # Modern BotSpec with identity
    modern = BotSpec(
        id="mod",
        name="ModernBot",
        identity=BotIdentitySpec(soul="SOUL.md", version=2),
    )
    assert modern.identity is not None
    assert modern.identity.version == 2
    d_mod = modern.to_dict()
    restored = BotSpec.from_dict(d_mod)
    assert restored.identity == modern.identity
