import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "optional-skills/creative/visual-explainer/scripts/visual_explainer.py"
spec = importlib.util.spec_from_file_location("visual_explainer", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_generates_and_verifies_valid_svg():
    svg = module.generate({"kind": "flow", "title": "A & B", "steps": [{"id": "a", "label": "Start"}]})
    assert "A &amp; B" in svg
    assert module.verify_svg(svg) == svg


def test_generation_is_deterministic():
    spec = {"kind": "flow", "title": "Example", "steps": [{"id": "a", "label": "One"}, {"id": "b", "label": "Two"}]}
    assert module.generate(spec) == module.generate(spec)


def test_connections_are_rendered():
    svg = module.generate(
        {
            "kind": "flow",
            "title": "Connected",
            "steps": [{"id": "a", "label": "One"}, {"id": "b", "label": "Two"}],
            "connections": [{"from": "a", "to": "b", "label": "next"}],
        }
    )
    assert "<line" in svg
    assert "next" in svg


@pytest.mark.parametrize("payload", [
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"><script>alert(1)</script></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"><rect onclick="alert(1)"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"><image href="https://evil.test/a.png"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg" width="0" height="100" viewBox="0 0 100 100"/>',
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"><unknown/></svg>',
])
def test_rejects_unsafe_svg(payload):
    with pytest.raises(module.ValidationError):
        module.verify_svg(payload)


def test_rejects_oversized_svg():
    with pytest.raises(module.ValidationError, match="maximum size"):
        module.verify_svg(" " * (module.MAX_BYTES + 1))


def test_rejects_excessive_node_count():
    body = "<g/>" * (module.MAX_NODES + 1)
    payload = f'<svg xmlns="{module.SVG_NS}" width="100" height="100" viewBox="0 0 100 100">{body}</svg>'
    with pytest.raises(module.ValidationError, match="node count"):
        module.verify_svg(payload)


def test_rejects_local_reference_not_in_allowlist():
    payload = f'<svg xmlns="{module.SVG_NS}" width="100" height="100" viewBox="0 0 100 100"><path marker-end="url(#missing)"/></svg>'
    with pytest.raises(module.ValidationError, match="allowlist"):
        module.verify_svg(payload)


def test_rejects_malformed_xml_and_invalid_viewbox():
    with pytest.raises(module.ValidationError, match="malformed"):
        module.verify_svg("<svg>")
    payload = f'<svg xmlns="{module.SVG_NS}" width="100" height="100" viewBox="0 0 nan 100"/>'
    with pytest.raises(module.ValidationError, match="viewBox"):
        module.verify_svg(payload)
