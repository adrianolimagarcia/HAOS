from hermes.platform.bots.identity import compute_bundle_hash, compute_sha256
from hermes.platform.bots.leaf_protocol import build_temporary_soul
from hermes.platform.bots.native_civ import (
    is_native_available,
    native_build_temporary_soul,
    native_compute_bundle_hash,
    native_compute_sha256,
)


def test_native_civ_parity():
    if not is_native_available():
        return

    text = "Civilization parity verification text"
    py_sha = compute_sha256(text)
    rust_sha = native_compute_sha256(text)
    assert py_sha == rust_sha

    soul = "DeepSeek System Mind"
    identity = "Hermes Turbo Bot"
    values = "Strict determinism and prompt stability"

    py_bundle_hash = compute_bundle_hash(soul, identity, values)
    rust_bundle_hash = native_compute_bundle_hash(soul, identity, values)
    assert py_bundle_hash == rust_bundle_hash

    constraints = ["No hallucination", "Preserve prompt cache"]
    py_temp_soul = build_temporary_soul(soul, "Audit Rust bridge", constraints, "Council #1")
    rust_temp_soul = native_build_temporary_soul(soul, "Audit Rust bridge", constraints, "Council #1")
    assert py_temp_soul == rust_temp_soul
