# ADR-005: Posture Model Binding

- **Status:** Accepted
- **Version:** 1.0.0
- **Date:** 2026-09-08
- **Authors:** Architecture Agent & Hermes Core Team

## Context
Different agent roles have fundamentally different capability requirements: an Architect needs deep reasoning, an Implementer needs fast code generation with large context, and a Witness Reviewer needs adversarial critical judgment. Hardcoding models per agent process limits agility.

## Decision
Models are bound to operational **Postures**, resolved through a strict hierarchical precedence order:
$$\text{Task Override} > \text{Posture} > \text{Agent Profile} > \text{Team Default} > \text{Global Config}$$
1. Postures (`architect`, `implementer`, `reviewer`, `security-reviewer`, `judge`, `vision`) specify their preferred `model_profile`.
2. The same physical agent can assume different postures across different tasks.
3. Concrete model names stay in configuration (`model_profiles.json`), never hardcoded in schemas or Python code.

## Alternatives Considered
- *Alternative A: One global model for all agent activities.* Rejected: prohibitively expensive or quality-deficient.
- *Alternative B: Model hardcoded directly inside agent prompt definitions.* Rejected: breaks prompt caching and requires code releases to update models.

## Consequences
- Cost-performance optimization: expensive models used only where needed (Architect/Judge); fast cost-effective models used for workers (`deepseek-v4-flash`).
- Dynamic reconfiguration without agent rebuild.
