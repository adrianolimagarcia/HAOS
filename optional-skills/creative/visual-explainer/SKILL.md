---
name: visual-explainer
description: Generate deterministic verifiable SVG explainers safely.
version: 0.1.0
author: Hermes Agent
license: MIT
dependencies: []
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [creative, visualization, svg, diagrams, explainer, deterministic]
    related_skills: [concept-diagrams, excalidraw]
---

# Visual Explainer Skill

Generate deterministic, verifiable SVG visual explainers from structured JSON specifications or validate standalone SVG diagrams with strict fail-closed security. It does not perform geometric layout inspection or collision checking without a real rendering engine.

## When to Use

Use this skill when:
- Creating deterministic vertical process flows, state transitions, or entity chains in SVG format.
- Validating and sanitizing untrusted or model-generated SVG diagrams before returning them to users or rendering them in web environments.
- Enforcing strict fail-closed constraints (strict SVG root, finite dimensions, no external resource leaks, zero scripts or event handlers).

Do not use this skill for:
- Detecting visual text overlap, typography collisions, or aesthetic readability that requires a browser layout engine or headless canvas.
- Complex animation timelines (prefer manim or dedicated video skills).

## Prerequisites

- Python 3.10+ (standard library only; no external packages or core tools required).
- Access to Hermes standard file operations (`read_file`, `write_file`) and `terminal` to run scripts.

## How to Run

Generate an SVG from a declarative JSON specification:
```bash
python optional-skills/creative/visual-explainer/scripts/visual_explainer.py generate --spec path/to/spec.json --output diagram.svg
```

Verify and sanitize an existing SVG file:
```bash
python optional-skills/creative/visual-explainer/scripts/visual_explainer.py verify --input diagram.svg
```

Pipe specification via stdin:
```bash
python optional-skills/creative/visual-explainer/scripts/visual_explainer.py generate --spec - --output diagram.svg < spec.json
```

## Quick Reference

Supported declarative template:

1. `flow`: Vertical step-by-step processes (`steps: [{"id", "label", "detail"}]`) with optional rendered connectors (`connections: [{"from", "to", "label"}]`).

Strict Fail-Closed Security Policy:
- Well-formed XML parsed via standard library `xml.etree.ElementTree`.
- Root element must strictly be `{http://www.w3.org/2000/svg}svg` or `<svg>`.
- Allowed attributes on `<svg>`: `xmlns`, `viewBox`, `width`, `height`, `class`, `style`.
- Size limits: maximum raw content length 128 KB (131,072 bytes), maximum node count 500 nodes.
- Disallowed elements: `script`, `foreignObject`, `iframe`, `object`, `embed`, `applet`, `audio`, `video`, `use` (unless referencing local defs IDs), `image` (external/data sources forbidden).
- Disallowed attributes: all event handlers (`on*`), remote URLs (`http:`, `https:`, `//`), raw `data:` URIs, external fragment identifiers.

## Procedure

1. Choose a flow with 1–30 steps. Use `references/spec-format.md` for the JSON shape.
2. Write the JSON specification to a file; V1 supports only `kind: "flow"`.
3. Generate using the command shown in `## How to Run`.
4. Verify the result using the `verify` command before delivery.
5. Inspect the SVG artifact. Verification checks syntax and policy only, not text overlap or visual legibility.

## Pitfalls

- **Geometric Overlaps**: Without a rendering engine (like Chromium or WebKit), text bounding boxes cannot be measured. Keep labels concise and adhere to standard spacing guidelines.
- **External references**: V1 allows only local fragments in marker-start/end/mid attributes, and only when the referenced SVG ID exists. Other `url(...)`, external URI, and data URI values are rejected.
- **Inline Event Handlers**: Any `onclick`, `onload`, or JavaScript pseudo-protocols (`javascript:`) will immediately fail validation.
- **Deterministic Layout**: The V1 flow renderer places steps in a fixed vertical list; identical JSON produces identical output bytes.

## Verification

Run verification against any SVG with:
```bash
python optional-skills/creative/visual-explainer/scripts/visual_explainer.py verify --input diagram.svg
```
Exit code 0 confirms valid, well-formed, safe SVG. Non-zero exit code outputs machine-readable JSON detailing validation errors.
