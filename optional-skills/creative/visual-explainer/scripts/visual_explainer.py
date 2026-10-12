#!/usr/bin/env python3
"""Deterministic, fail-closed SVG explainer generator and verifier (stdlib only)."""
from __future__ import annotations
import argparse
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
MAX_BYTES = 131_072
MAX_NODES = 500
MAX_DIMENSION = 4096
MAX_COORDINATE = 100_000
ELEMENTS = {"svg", "g", "defs", "marker", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "title", "desc"}
ATTRIBUTES = {"xmlns", "viewBox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "d", "points", "fill", "fill-opacity", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "opacity", "font-family", "font-size", "font-weight", "text-anchor", "dominant-baseline", "class", "id", "transform", "marker-start", "marker-mid", "marker-end", "orient", "markerWidth", "markerHeight", "refX", "refY", "preserveAspectRatio", "role", "aria-label", "aria-labelledby", "aria-describedby"}
NUM = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")

class ValidationError(ValueError):
    """Input violates the SVG safety contract."""

def _dimension(value: Any, label: str) -> float:
    if not isinstance(value, str) or not NUM.fullmatch(value):
        raise ValidationError(f"invalid {label}")
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number > MAX_DIMENSION:
        raise ValidationError(f"invalid {label}")
    return number

def verify_svg(svg: str) -> str:
    """Parse and enforce a strict SVG subset; return canonicalized SVG text."""
    if not isinstance(svg, str) or len(svg.encode("utf-8")) > MAX_BYTES:
        raise ValidationError("SVG exceeds maximum size")
    try:
        root = ET.fromstring(svg)
    except (ET.ParseError, ValueError) as exc:
        raise ValidationError("malformed XML") from exc
    if root.tag not in (f"{{{SVG_NS}}}svg", "svg"):
        raise ValidationError("root must be SVG")
    ids: set[str] = set()
    refs: list[str] = []
    count = 0
    for element in root.iter():
        count += 1
        if count > MAX_NODES:
            raise ValidationError("SVG exceeds maximum node count")
        tag = element.tag.rsplit("}", 1)[-1]
        if tag not in ELEMENTS:
            raise ValidationError(f"disallowed element: {tag}")
        for raw_name, value in element.attrib.items():
            name = raw_name.rsplit("}", 1)[-1]
            if name.lower().startswith("on"):
                raise ValidationError("event handlers are forbidden")
            if name not in ATTRIBUTES:
                raise ValidationError(f"disallowed attribute: {name}")
            if name == "id":
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", value) or value in ids:
                    raise ValidationError("invalid or duplicate id")
                ids.add(value)
            if name in {"marker-start", "marker-mid", "marker-end"}:
                match = re.fullmatch(r"url\(#([A-Za-z_][A-Za-z0-9_.-]{0,63})\)", value)
                if not match:
                    raise ValidationError("reference is not an allowed local fragment")
                refs.append(match.group(1))
                continue
            low = value.strip().lower()
            if any(token in low for token in ("url(", "data:", "javascript:", "http:", "https:", "//")):
                raise ValidationError(f"external or active reference in {name}")
        if element.text and "javascript:" in element.text.lower():
            raise ValidationError("active text is forbidden")
    if any(ref not in ids for ref in refs):
        raise ValidationError("reference outside SVG allowlist")
    _dimension(root.attrib.get("width"), "width")
    _dimension(root.attrib.get("height"), "height")
    parts = root.attrib.get("viewBox", "").split()
    if len(parts) != 4 or any(not NUM.fullmatch(part) or not math.isfinite(float(part)) for part in parts):
        raise ValidationError("invalid viewBox")
    x, y, vb_width, vb_height = map(float, parts)
    if max(abs(x), abs(y), vb_width, vb_height) > MAX_COORDINATE or vb_width <= 0 or vb_height <= 0:
        raise ValidationError("invalid viewBox bounds")
    return ET.tostring(root, encoding="unicode", short_empty_elements=True)

def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) > 160:
        raise ValidationError(f"{field} must be a string of at most 160 characters")
    return value

def _esc(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")

def generate(spec: dict[str, Any]) -> str:
    """Render a simple deterministic flow diagram from validated JSON."""
    if not isinstance(spec, dict) or set(spec) - {"kind", "title", "steps", "connections"}:
        raise ValidationError("spec must contain only kind, title, steps, and connections")
    if spec.get("kind") != "flow":
        raise ValidationError("V1 generator supports only kind='flow'")
    title = _text(spec.get("title", "Visual explanation"), "title")
    steps, connections = spec.get("steps"), spec.get("connections", [])
    if not isinstance(steps, list) or not 1 <= len(steps) <= 30 or not isinstance(connections, list) or len(connections) > 100:
        raise ValidationError("steps must contain 1-30 entries and connections at most 100")
    normalized, ids = [], set()
    for item in steps:
        if not isinstance(item, dict) or set(item) - {"id", "label", "detail"}:
            raise ValidationError("step must contain only id, label, and detail")
        ident = _text(item.get("id"), "id")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", ident) or ident in ids:
            raise ValidationError("invalid or duplicate step id")
        ids.add(ident)
        normalized.append((ident, _text(item.get("label"), "label"), _text(item.get("detail", ""), "detail")))
    normalized_connections = []
    for connection in connections:
        if not isinstance(connection, dict) or set(connection) - {"from", "to", "label"} or connection.get("from") not in ids or connection.get("to") not in ids:
            raise ValidationError("connection must reference known step ids")
        normalized_connections.append(
            (connection["from"], connection["to"], _text(connection.get("label", ""), "connection label"))
        )
    width, height = 800, max(160, 80 + len(normalized) * 100)
    bits = [f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">', f'<title>{_esc(title)}</title>']
    positions = {ident: 76 + index * 100 for index, (ident, _, _) in enumerate(normalized)}
    for source, target, label in normalized_connections:
        y1, y2 = positions[source], positions[target]
        bits.append(f'<line x1="400" y1="{y1}" x2="400" y2="{y2}" stroke="#475569" stroke-width="2"/>')
        if label:
            bits.append(f'<text x="410" y="{(y1 + y2) // 2}" font-family="system-ui, sans-serif" font-size="11">{_esc(label)}</text>')
    for index, (_, label, detail) in enumerate(normalized):
        y = 40 + index * 100
        bits.append(f'<rect x="120" y="{y}" width="560" height="72" rx="8" fill="#dbeafe" stroke="#2563eb"/>')
        bits.append(f'<text x="400" y="{y + 30}" text-anchor="middle" font-family="system-ui, sans-serif" font-size="18">{_esc(label)}</text>')
        if detail:
            bits.append(f'<text x="400" y="{y + 54}" text-anchor="middle" font-family="system-ui, sans-serif" font-size="12">{_esc(detail)}</text>')
    bits.append("</svg>")
    return verify_svg("".join(bits))

def _load_spec(path: str) -> dict[str, Any]:
    try:
        raw = sys.stdin.read(MAX_BYTES + 1) if path == "-" else Path(path).read_text(encoding="utf-8")
        if len(raw.encode("utf-8")) > MAX_BYTES:
            raise ValidationError("spec exceeds maximum size")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot read JSON specification: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError("specification must be a JSON object")
    return value

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("generate")
    gen.add_argument("--spec", required=True)
    gen.add_argument("--output", required=True)
    check = commands.add_parser("verify")
    check.add_argument("--input", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            svg = generate(_load_spec(args.spec))
            Path(args.output).write_text(svg + "\n", encoding="utf-8")
            print(json.dumps({"ok": True, "bytes": len(svg.encode("utf-8"))}, sort_keys=True))
        else:
            canonical = verify_svg(Path(args.input).read_text(encoding="utf-8"))
            print(json.dumps({"ok": True, "bytes": len(canonical.encode("utf-8"))}, sort_keys=True))
        return 0
    except (ValidationError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
