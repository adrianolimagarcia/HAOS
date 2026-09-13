# ACR-0001: Initial Architecture Freeze v0.1

- **ACR ID:** ACR-0001
- **Title:** Baseline Freeze of Core Platform Schemas & Contracts
- **Author:** Architecture Agent
- **Date:** 2026-09-08
- **Status:** ACCEPTED
- **Impacted Schemas:**
  - `TaskSpec`
  - `TaskRun`
  - `TaskResult`
  - `AssignmentSpec`
  - `ModelProfile`
  - `CapabilitySpec`
  - `ContextPackage`
  - `MemoryItem`
  - `ArtifactSpec`

## Rationale
Prevents independent agents from deviating into diverging schemas during implementation of Epics M0-M20. Changes to these schemas must now be submitted as a formal Architecture Change Request (ACR).

## Backward Compatibility
Preserved 100%. All contracts enforce `schema_version = "1.0.0"`.
Minor optional fields may be added additively.
Renames, deletions, or structural shifts require a new major version.
