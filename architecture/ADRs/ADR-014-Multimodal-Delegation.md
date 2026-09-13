# ADR-014: Multimodal Delegation

- **Status:** Accepted
- **Version:** 1.0.0
- **Date:** 2026-09-08
- **Authors:** Architecture Agent & Hermes Core Team

## Context
Standard text-focused models (like `deepseek-v4-flash`) are exceptionally cost-effective and capable at coding, but lack native vision, OCR, or audio modalities. Routing all coding tasks through massive, expensive multimodal frontier models wastes tokens and degrades specialized coding quality.

## Decision
Missing modality capabilities generate **asynchronous sub-worker delegation**:
1. When a text worker encounters an image, UI mockup, or diagram, it issues a `CapabilityRequest(vision)`.
2. The runtime pauses the text worker and dispatches a dedicated `VisionWorker` equipped with a multimodal model (`vision-primary`).
3. The Vision Worker processes the asset and returns a structured `PerceptionArtifact` (containing layout, OCR text, bounding boxes, and semantic observations).
4. The text worker resumes execution using the lightweight `PerceptionArtifact` without switching its primary model or bloating its prompt cache.

## Alternatives Considered
- *Alternative A: Switch the primary coder agent permanently to a multimodal model.* Rejected: 5x–10x higher cost and reduced coding benchmark performance.
- *Alternative B: Fail closed when a non-multimodal model encounters an image.* Rejected: limits automation of UI/UX and full-stack tasks.

## Consequences
- Preserves the low cost and high speed of specialized text/coding models.
- Modular, pluggable perception backends (Vision, OCR, Audio, Video, ImageGen).
