# Visual Explainer Specification Reference

V1 supports only deterministic vertical flow diagrams. It renders step boxes and optional labeled connectors. It does not verify rendered overlap, font metrics, or visual legibility.

## Design constraints

- Deterministic coordinates and output for identical input.
- Fail-closed SVG verification for scripts, event handlers, external references, invalid dimensions, and excessive size/node count.
- Python standard library only.

## Flow schema

```json
{
  "kind": "flow",
  "title": "Data Processing Flow",
  "steps": [
    {"id": "step1", "label": "Ingest", "detail": "Read JSON or stream"},
    {"id": "step2", "label": "Validate", "detail": "Fail-closed check"},
    {"id": "step3", "label": "Render", "detail": "Deterministic SVG output"}
  ],
  "connections": [
    {"from": "step1", "to": "step2", "label": "valid data"},
    {"from": "step2", "to": "step3"}
  ]
}
```

Rules:

- `kind` must be `flow`.
- `steps` contains 1–30 unique IDs; each item may contain only `id`, `label`, and `detail`.
- `connections` is optional, contains at most 100 entries, and may contain only `from`, `to`, and `label`.
- Both ends of every connection must reference a known step ID.
- Text fields are limited to 160 characters.
